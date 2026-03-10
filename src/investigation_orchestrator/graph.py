from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import StateSnapshot

from .checkpointing import GraphCheckpointConfig, build_graph_config
from .graph_nodes import (
    OrchestratorRuntimeDeps,
    apply_exploration_review_node,
    advance_batch_node,
    collect_external_steps_node,
    ensure_context_node,
    load_active_batch_node,
    prepare_exploration_review_node,
    render_report_node,
)
from .runtime_logging import log_graph_node, log_graph_run
from .state import OrchestrationState


def _state_read_checkpoint_config(checkpoint_config: GraphCheckpointConfig) -> GraphCheckpointConfig:
    return GraphCheckpointConfig(
        thread_id=checkpoint_config.thread_id,
        checkpoint_ns=checkpoint_config.checkpoint_ns,
    )


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
    return "collect_external_steps"


def _route_after_collect_external_steps(state: OrchestrationState) -> str:
    if state["pending_exploration_review"] is not None:
        return "prepare_exploration_review"
    return "advance_batch"


def _logged_node(node_name: str, node_fn, *, checkpoint_config: GraphCheckpointConfig | None):
    def _runner(state: OrchestrationState) -> dict:
        log_graph_node(
            event="enter",
            node=node_name,
            checkpoint_config=checkpoint_config,
            state=state,
        )
        try:
            result = node_fn(state)
        except Exception as exc:
            log_graph_node(
                event="failure",
                node=node_name,
                checkpoint_config=checkpoint_config,
                state=state,
                error_type=type(exc).__name__,
            )
            raise
        merged_state = {**state, **result}
        log_graph_node(
            event="exit",
            node=node_name,
            checkpoint_config=checkpoint_config,
            state=merged_state,
        )
        return result

    return _runner


def build_investigation_graph(
    *,
    deps: OrchestratorRuntimeDeps,
    checkpointer: BaseCheckpointSaver | None = None,
    checkpoint_config: GraphCheckpointConfig | None = None,
    interrupt_before: tuple[str, ...] | list[str] = (),
    interrupt_after: tuple[str, ...] | list[str] = (),
    enable_exploration_review_interrupt: bool = False,
):
    internal_interrupt_after = list(interrupt_after)
    if enable_exploration_review_interrupt:
        internal_interrupt_after = [*internal_interrupt_after, "prepare_exploration_review"]
        internal_interrupt_after = list(dict.fromkeys(internal_interrupt_after))

    graph = StateGraph(OrchestrationState)
    graph.add_node(
        "ensure_context",
        _logged_node("ensure_context", lambda state: ensure_context_node(state, deps), checkpoint_config=checkpoint_config),
    )
    graph.add_node(
        "load_active_batch",
        _logged_node("load_active_batch", lambda state: load_active_batch_node(state, deps), checkpoint_config=checkpoint_config),
    )
    graph.add_node(
        "collect_external_steps",
        _logged_node(
            "collect_external_steps",
            lambda state: collect_external_steps_node(state, deps),
            checkpoint_config=checkpoint_config,
        ),
    )
    graph.add_node(
        "prepare_exploration_review",
        _logged_node(
            "prepare_exploration_review",
            lambda state: prepare_exploration_review_node(state, deps),
            checkpoint_config=checkpoint_config,
        ),
    )
    graph.add_node(
        "apply_exploration_review",
        _logged_node(
            "apply_exploration_review",
            lambda state: apply_exploration_review_node(state, deps),
            checkpoint_config=checkpoint_config,
        ),
    )
    graph.add_node(
        "advance_batch",
        _logged_node("advance_batch", lambda state: advance_batch_node(state, deps), checkpoint_config=checkpoint_config),
    )
    graph.add_node(
        "render_report",
        _logged_node("render_report", lambda state: render_report_node(state, deps), checkpoint_config=checkpoint_config),
    )

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
            "collect_external_steps": "collect_external_steps",
            "render_report": "render_report",
        },
    )
    graph.add_conditional_edges(
        "collect_external_steps",
        _route_after_collect_external_steps,
        {
            "prepare_exploration_review": "prepare_exploration_review",
            "advance_batch": "advance_batch",
        },
    )
    graph.add_edge("prepare_exploration_review", "apply_exploration_review")
    graph.add_edge("apply_exploration_review", "advance_batch")
    graph.add_edge("advance_batch", "ensure_context")
    graph.add_edge("render_report", END)

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=list(interrupt_before),
        interrupt_after=internal_interrupt_after,
    )


def invoke_investigation_graph(
    initial_state: OrchestrationState,
    *,
    deps: OrchestratorRuntimeDeps,
    checkpointer: BaseCheckpointSaver | None = None,
    checkpoint_config: GraphCheckpointConfig | None = None,
    interrupt_before: tuple[str, ...] | list[str] = (),
    interrupt_after: tuple[str, ...] | list[str] = (),
    enable_exploration_review_interrupt: bool = False,
) -> OrchestrationState:
    if checkpoint_config is not None and checkpointer is None:
        raise ValueError("checkpoint_config requires a checkpointer")
    if checkpointer is not None and checkpoint_config is None:
        raise ValueError("checkpoint_config with an explicit thread_id is required when checkpointing is enabled")

    graph = build_investigation_graph(
        deps=deps,
        checkpointer=checkpointer,
        checkpoint_config=checkpoint_config,
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
        enable_exploration_review_interrupt=enable_exploration_review_interrupt,
    )
    config = build_graph_config(checkpoint_config) if checkpointer else None
    log_graph_run(
        mode="invoke",
        status="start",
        checkpoint_config=checkpoint_config,
        state=initial_state,
    )
    try:
        result = graph.invoke(initial_state, config=config)
    except Exception as exc:
        log_graph_run(
            mode="invoke",
            status="failure",
            checkpoint_config=checkpoint_config,
            state=initial_state,
            error_type=type(exc).__name__,
        )
        raise
    if checkpointer is not None:
        snapshot = graph.get_state(build_graph_config(_state_read_checkpoint_config(checkpoint_config)))
        if snapshot.next:
            log_graph_run(
                mode="invoke",
                status="interrupted",
                checkpoint_config=checkpoint_config,
                state=snapshot.values,
                next_nodes=snapshot.next,
            )
            return snapshot.values
    log_graph_run(
        mode="invoke",
        status="success",
        checkpoint_config=checkpoint_config,
        state=result,
    )
    return result


def resume_investigation_graph(
    *,
    deps: OrchestratorRuntimeDeps,
    checkpointer: BaseCheckpointSaver,
    checkpoint_config: GraphCheckpointConfig,
    interrupt_before: tuple[str, ...] | list[str] = (),
    interrupt_after: tuple[str, ...] | list[str] = (),
    enable_exploration_review_interrupt: bool = False,
) -> OrchestrationState:
    graph = build_investigation_graph(
        deps=deps,
        checkpointer=checkpointer,
        checkpoint_config=checkpoint_config,
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
        enable_exploration_review_interrupt=enable_exploration_review_interrupt,
    )
    config = build_graph_config(checkpoint_config)
    snapshot = graph.get_state(build_graph_config(_state_read_checkpoint_config(checkpoint_config)))
    if not snapshot.values:
        raise ValueError("no resumable graph state exists for the requested thread_id")
    if not snapshot.next:
        raise ValueError("graph has no resumable next node for the requested thread_id")
    log_graph_run(
        mode="resume",
        status="start",
        checkpoint_config=checkpoint_config,
        state=snapshot.values,
        next_nodes=snapshot.next,
    )
    try:
        result = graph.invoke(None, config=config)
    except Exception as exc:
        log_graph_run(
            mode="resume",
            status="failure",
            checkpoint_config=checkpoint_config,
            state=snapshot.values,
            next_nodes=snapshot.next,
            error_type=type(exc).__name__,
        )
        raise
    resumed_snapshot = graph.get_state(build_graph_config(_state_read_checkpoint_config(checkpoint_config)))
    if resumed_snapshot.next:
        log_graph_run(
            mode="resume",
            status="interrupted",
            checkpoint_config=checkpoint_config,
            state=resumed_snapshot.values,
            next_nodes=resumed_snapshot.next,
        )
        return resumed_snapshot.values
    log_graph_run(
        mode="resume",
        status="success",
        checkpoint_config=checkpoint_config,
        state=result,
    )
    return result


def get_investigation_graph_state(
    *,
    deps: OrchestratorRuntimeDeps,
    checkpointer: BaseCheckpointSaver,
    checkpoint_config: GraphCheckpointConfig,
    interrupt_before: tuple[str, ...] | list[str] = (),
    interrupt_after: tuple[str, ...] | list[str] = (),
    enable_exploration_review_interrupt: bool = False,
) -> StateSnapshot:
    graph = build_investigation_graph(
        deps=deps,
        checkpointer=checkpointer,
        checkpoint_config=checkpoint_config,
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
        enable_exploration_review_interrupt=enable_exploration_review_interrupt,
    )
    return graph.get_state(build_graph_config(checkpoint_config))


def update_investigation_graph_state(
    *,
    deps: OrchestratorRuntimeDeps,
    checkpointer: BaseCheckpointSaver,
    checkpoint_config: GraphCheckpointConfig,
    values: dict[str, object],
    as_node: str | None = None,
    interrupt_before: tuple[str, ...] | list[str] = (),
    interrupt_after: tuple[str, ...] | list[str] = (),
    enable_exploration_review_interrupt: bool = False,
) -> OrchestrationState:
    graph = build_investigation_graph(
        deps=deps,
        checkpointer=checkpointer,
        checkpoint_config=checkpoint_config,
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
        enable_exploration_review_interrupt=enable_exploration_review_interrupt,
    )
    config = build_graph_config(checkpoint_config)
    graph.update_state(config, values, as_node=as_node)
    snapshot = graph.get_state(build_graph_config(_state_read_checkpoint_config(checkpoint_config)))
    return snapshot.values
