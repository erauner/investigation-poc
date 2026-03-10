from __future__ import annotations

import httpx
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from investigation_orchestrator.checkpointing import create_in_memory_checkpointer

from .checkpoint_adapter import ShadowKAgentCheckpointer
from .runner import run_shadow_investigation
from .settings import get_shadow_checkpoint_mode


def _last_user_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        content = getattr(message, "content", "")
        if isinstance(content, str) and content.strip():
            return content
    raise ValueError("shadow runtime requires a non-empty user task")


def _run_shadow_node(state: MessagesState) -> dict[str, list[AIMessage]]:
    task = _last_user_text(state["messages"])
    result = run_shadow_investigation(task)
    return {"messages": [AIMessage(content=result.markdown)]}


def build_shadow_graph():
    from kagent.core import KAgentConfig

    if get_shadow_checkpoint_mode() == "memory":
        checkpointer = create_in_memory_checkpointer()
    else:
        config = KAgentConfig()
        checkpointer = ShadowKAgentCheckpointer(
            client=httpx.AsyncClient(base_url=config.url),
            app_name=config.app_name,
        )

    graph = StateGraph(MessagesState)
    graph.add_node("run_shadow_investigation", _run_shadow_node)
    graph.add_edge(START, "run_shadow_investigation")
    graph.add_edge("run_shadow_investigation", END)
    return graph.compile(checkpointer=checkpointer)


graph = build_shadow_graph()
