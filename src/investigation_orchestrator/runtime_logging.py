import json
import logging
import hashlib
from typing import Any

from .checkpointing import GraphCheckpointConfig
from .state import OrchestrationState


_LOGGER = logging.getLogger("investigation_orchestrator.runtime")


def _ensure_logger() -> logging.Logger:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        _LOGGER.setLevel(logging.INFO)
        _LOGGER.propagate = True
        return _LOGGER
    if not _LOGGER.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        _LOGGER.addHandler(handler)
        _LOGGER.setLevel(logging.INFO)
        _LOGGER.propagate = False
    return _LOGGER


def _checkpoint_summary(checkpoint_config: GraphCheckpointConfig | None) -> dict[str, Any]:
    thread_id = checkpoint_config.thread_id if checkpoint_config else None
    checkpoint_ns = checkpoint_config.checkpoint_ns if checkpoint_config else None
    return {
        "has_thread_id": bool(thread_id),
        "thread_id_token": hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:12] if thread_id else None,
        "has_checkpoint_ns": bool(checkpoint_ns),
        "checkpoint_ns_token": hashlib.sha256(checkpoint_ns.encode("utf-8")).hexdigest()[:12] if checkpoint_ns else None,
        "has_checkpoint_id": bool(checkpoint_config.checkpoint_id) if checkpoint_config else False,
    }


def summarize_graph_state(state: OrchestrationState | dict[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    execution_context = state.get("execution_context")
    active_batch = state.get("active_batch")
    final_report = state.get("final_report")
    pending_review = state.get("pending_exploration_review")
    return {
        "has_execution_context": execution_context is not None,
        "plan_has_active_batch": bool(execution_context.updated_plan.active_batch_id) if execution_context else False,
        "active_batch_present": active_batch is not None,
        "active_batch_id": active_batch.batch_id if active_batch is not None else None,
        "submitted_steps_count": len(state.get("submitted_steps") or []),
        "pending_exploration_review": pending_review is not None,
        "pending_review_step_id": pending_review.step.step_id if pending_review is not None else None,
        "pending_review_capability": pending_review.capability if pending_review is not None else None,
        "pending_review_decision": pending_review.decision if pending_review is not None else None,
        "pending_review_adequacy_outcome": pending_review.adequacy_outcome if pending_review is not None else None,
        "remaining_batch_budget": state.get("remaining_batch_budget"),
        "has_final_report": final_report is not None,
    }


def log_graph_run(
    *,
    mode: str,
    status: str,
    checkpoint_config: GraphCheckpointConfig | None,
    state: OrchestrationState | dict[str, Any] | None = None,
    next_nodes: tuple[str, ...] | list[str] | None = None,
    error_type: str | None = None,
) -> None:
    logger = _ensure_logger()
    summary = {
        **_checkpoint_summary(checkpoint_config),
        **summarize_graph_state(state),
        "next_nodes": list(next_nodes or []),
    }
    encoded_summary = json.dumps(summary, sort_keys=True)
    if status == "failure":
        logger.warning(
            "orchestrator_graph_run mode=%s status=%s error_type=%s summary=%s",
            mode,
            status,
            error_type,
            encoded_summary,
        )
        return
    logger.info("orchestrator_graph_run mode=%s status=%s summary=%s", mode, status, encoded_summary)


def log_graph_node(
    *,
    event: str,
    node: str,
    checkpoint_config: GraphCheckpointConfig | None,
    state: OrchestrationState | dict[str, Any] | None,
    error_type: str | None = None,
) -> None:
    logger = _ensure_logger()
    summary = {
        **_checkpoint_summary(checkpoint_config),
        **summarize_graph_state(state),
    }
    encoded_summary = json.dumps(summary, sort_keys=True)
    if event == "failure":
        logger.warning(
            "orchestrator_graph_node event=%s node=%s error_type=%s summary=%s",
            event,
            node,
            error_type,
            encoded_summary,
        )
        return
    logger.info("orchestrator_graph_node event=%s node=%s summary=%s", event, node, encoded_summary)
