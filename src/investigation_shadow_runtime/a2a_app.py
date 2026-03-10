from __future__ import annotations

import faulthandler
import logging
import uuid
from datetime import UTC, datetime

from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCard,
    Artifact,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from kagent.core import KAgentConfig, configure_tracing
from kagent.core.a2a import KAgentRequestContextBuilder, get_a2a_max_content_length
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)


def health_check(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")


def thread_dump(request: Request) -> PlainTextResponse:
    import io

    buf = io.StringIO()
    faulthandler.dump_traceback(file=buf)
    buf.seek(0)
    return PlainTextResponse(buf.read())


def _text_message(*, task_id: str, context_id: str, text: str) -> Message:
    return Message(
        role=Role.agent,
        messageId=str(uuid.uuid4()),
        taskId=task_id,
        contextId=context_id,
        parts=[Part(root=TextPart(text=text))],
    )


class ShadowGraphExecutor(AgentExecutor):
    def __init__(self, *, graph: CompiledStateGraph, app_name: str):
        self._graph = graph
        self._app_name = app_name

    def _graph_config(self, context: RequestContext) -> dict:
        session_id = getattr(context, "session_id", None) or context.context_id
        return {
            "configurable": {
                "thread_id": session_id,
                "app_name": self._app_name,
            }
        }

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            raise ValueError("shadow runtime requires task_id and context_id")

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                taskId=task_id,
                contextId=context_id,
                final=False,
                status=TaskStatus(
                    state=TaskState.working,
                    timestamp=datetime.now(UTC).isoformat(),
                    message=_text_message(
                        task_id=task_id,
                        context_id=context_id,
                        text="Running shadow investigation runtime.",
                    ),
                ),
            )
        )

        try:
            result = await self._graph.ainvoke(
                {"messages": [HumanMessage(content=context.get_user_input())]},
                config=self._graph_config(context),
            )
            messages = result.get("messages", [])
            final_message = next(
                (
                    message
                    for message in reversed(messages)
                    if isinstance(message, AIMessage)
                    and isinstance(message.content, str)
                    and message.content.strip()
                ),
                None,
            )
            if final_message is None:
                raise ValueError("shadow runtime completed without an AI response")

            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    taskId=task_id,
                    contextId=context_id,
                    lastChunk=True,
                    artifact=Artifact(
                        artifactId=str(uuid.uuid4()),
                        parts=[Part(root=TextPart(text=final_message.content))],
                    ),
                )
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    taskId=task_id,
                    contextId=context_id,
                    final=True,
                    status=TaskStatus(
                        state=TaskState.completed,
                        timestamp=datetime.now(UTC).isoformat(),
                    ),
                )
            )
        except Exception as exc:
            logger.exception("shadow runtime execution failed")
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    taskId=task_id,
                    contextId=context_id,
                    final=True,
                    status=TaskStatus(
                        state=TaskState.failed,
                        timestamp=datetime.now(UTC).isoformat(),
                        message=_text_message(
                            task_id=task_id,
                            context_id=context_id,
                            text=f"Shadow runtime failed: {exc}",
                        ),
                    ),
                )
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            raise ValueError("shadow runtime requires task_id and context_id")
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                taskId=task_id,
                contextId=context_id,
                final=True,
                status=TaskStatus(
                    state=TaskState.canceled,
                    timestamp=datetime.now(UTC).isoformat(),
                ),
            )
        )


def build_shadow_app(
    *,
    graph: CompiledStateGraph,
    agent_card: dict,
    config: KAgentConfig,
    tracing: bool = True,
) -> FastAPI:
    task_store = InMemoryTaskStore()
    request_handler = DefaultRequestHandler(
        agent_executor=ShadowGraphExecutor(graph=graph, app_name=config.app_name),
        task_store=task_store,
        request_context_builder=KAgentRequestContextBuilder(task_store=task_store),
    )
    a2a_app = A2AStarletteApplication(
        agent_card=AgentCard.model_validate(agent_card),
        http_handler=request_handler,
        max_content_length=get_a2a_max_content_length(),
    )

    faulthandler.enable()

    app = FastAPI(
        title=f"KAgent LangGraph Shadow: {config.app_name}",
        description="Shadow BYO investigation runtime backed directly by the orchestrator library.",
        version="0.1.0",
    )
    if tracing:
        try:
            configure_tracing(app)
            logger.info("Tracing configured for shadow runtime")
        except Exception:
            logger.exception("Failed to configure tracing")

    app.add_route("/health", methods=["GET"], route=health_check)
    app.add_route("/thread_dump", methods=["GET"], route=thread_dump)
    a2a_app.add_routes_to_app(app)
    return app
