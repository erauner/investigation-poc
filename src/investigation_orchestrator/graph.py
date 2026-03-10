from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import StateSnapshot

from .checkpointing import GraphCheckpointConfig, build_graph_config
from .graph_nodes import (
    OrchestratorRuntimeDeps,
    advance_batch_node,
    ensure_context_node,
    load_active_batch_node,
    render_report_node,
    run_external_steps_node,
)
from .state import OrchestrationState


def _route_after_context(
    state: OrchestrationState,
    deps: OrchestratorRuntimeDeps,
) -> str:
    execution_context = state["execution_context"]
    if execution_context is None:
        raise ValueError("execution context must be initialized before routing")

    plan = execution_context.updated_plan
    if plan.active_batch_id is None or deps.active_batch_is_render_only(plan):
        return "render_report"
    if state["remaining_batch_budget"] <= 0:
        raise ValueError("orchestrator stopped with non-render work still pending")
    return "load_active_batch"


def _route_after_load_active_batch(state: OrchestrationState) -> str:
    if state["active_batch"] is None:
        return "render_report"
    return "run_external_steps"


def build_investigation_graph(
    *,
    deps: OrchestratorRuntimeDeps,
    checkpointer: BaseCheckpointSaver | None = None,
):
    graph = StateGraph(OrchestrationState)
    graph.add_node("ensure_context", lambda state: ensure_context_node(state, deps))
    graph.add_node("load_active_batch", lambda state: load_active_batch_node(state, deps))
    graph.add_node("run_external_steps", lambda state: run_external_steps_node(state, deps))
    graph.add_node("advance_batch", lambda state: advance_batch_node(state, deps))
    graph.add_node("render_report", lambda state: render_report_node(state, deps))

    graph.add_edge(START, "ensure_context")
    graph.add_conditional_edges(
        "ensure_context",
        lambda state: _route_after_context(state, deps),
        {
            "load_active_batch": "load_active_batch",
            "render_report": "render_report",
        },
    )
    graph.add_conditional_edges(
        "load_active_batch",
        _route_after_load_active_batch,
        {
            "run_external_steps": "run_external_steps",
            "render_report": "render_report",
        },
    )
    graph.add_edge("run_external_steps", "advance_batch")
    graph.add_edge("advance_batch", "ensure_context")
    graph.add_edge("render_report", END)

    return graph.compile(checkpointer=checkpointer)


def invoke_investigation_graph(
    initial_state: OrchestrationState,
    *,
    deps: OrchestratorRuntimeDeps,
    checkpointer: BaseCheckpointSaver | None = None,
    checkpoint_config: GraphCheckpointConfig | None = None,
) -> OrchestrationState:
    if checkpoint_config is not None and checkpointer is None:
        raise ValueError("checkpoint_config requires a checkpointer")
    if checkpointer is not None and checkpoint_config is None:
        raise ValueError("checkpoint_config with an explicit thread_id is required when checkpointing is enabled")

    graph = build_investigation_graph(deps=deps, checkpointer=checkpointer)
    config = build_graph_config(checkpoint_config) if checkpointer else None
    return graph.invoke(initial_state, config=config)


def get_investigation_graph_state(
    *,
    deps: OrchestratorRuntimeDeps,
    checkpointer: BaseCheckpointSaver,
    checkpoint_config: GraphCheckpointConfig,
) -> StateSnapshot:
    graph = build_investigation_graph(deps=deps, checkpointer=checkpointer)
    return graph.get_state(build_graph_config(checkpoint_config))
