from dataclasses import dataclass
from typing import Callable

from investigation_service.models import (
    ActiveEvidenceBatchContract,
    AdvanceInvestigationRuntimeResponse,
    InvestigationPlan,
    InvestigationReport,
    InvestigationReportRequest,
    ReportingExecutionContext,
    SubmittedStepArtifact,
)

from .evidence_runner import ExternalStepCollectionResult
from .state import OrchestrationState, PendingExplorationReview


@dataclass(frozen=True)
class OrchestratorRuntimeDeps:
    seed_context: Callable[..., ReportingExecutionContext]
    get_active_batch: Callable[..., ActiveEvidenceBatchContract | None]
    advance_batch: Callable[..., AdvanceInvestigationRuntimeResponse]
    render_report: Callable[[InvestigationReportRequest, ReportingExecutionContext], InvestigationReport]
    collect_external_steps: Callable[[ActiveEvidenceBatchContract], ExternalStepCollectionResult]
    apply_pending_exploration_review: Callable[[PendingExplorationReview], SubmittedStepArtifact]
    active_batch_is_render_only: Callable[[InvestigationPlan], bool]


def ensure_context_node(
    state: OrchestrationState,
    deps: OrchestratorRuntimeDeps,
) -> dict[str, ReportingExecutionContext]:
    if state["execution_context"] is not None:
        return {"execution_context": state["execution_context"]}

    return {
        "execution_context": deps.seed_context(
            state["incident"],
            allow_bounded_fallback_execution=False,
        )
    }


def load_active_batch_node(
    state: OrchestrationState,
    deps: OrchestratorRuntimeDeps,
) -> dict[str, ActiveEvidenceBatchContract | None]:
    execution_context = state["execution_context"]
    if execution_context is None:
        raise ValueError("execution context must be initialized before loading active batch")

    return {
        "active_batch": deps.get_active_batch(
            state["incident"],
            execution_context,
        )
    }


def collect_external_steps_node(
    state: OrchestrationState,
    deps: OrchestratorRuntimeDeps,
) -> dict[str, list[SubmittedStepArtifact] | PendingExplorationReview | None]:
    active_batch = state["active_batch"]
    if active_batch is None:
        raise ValueError("active batch must be present before collecting external steps")

    collection_result = deps.collect_external_steps(active_batch)
    submitted_steps = collection_result.submitted_steps
    # Workload/service/node transport may record peer-attempt metadata and let
    # planner-owned bounded fallback execute for the same external-preferred step.
    if (
        any(step.execution_mode == "external_preferred" for step in active_batch.steps)
        and not submitted_steps
        and collection_result.pending_exploration_review is None
    ):
        raise ValueError("required external steps were not materialized")

    return {
        "submitted_steps": submitted_steps,
        "pending_exploration_review": collection_result.pending_exploration_review,
    }


def prepare_exploration_review_node(
    state: OrchestrationState,
    _deps: OrchestratorRuntimeDeps,
) -> dict[str, PendingExplorationReview]:
    pending_review = state["pending_exploration_review"]
    if pending_review is None:
        raise ValueError("pending exploration review must be present before pausing for review")
    return {"pending_exploration_review": pending_review}


def apply_exploration_review_node(
    state: OrchestrationState,
    deps: OrchestratorRuntimeDeps,
) -> dict[str, list[SubmittedStepArtifact] | PendingExplorationReview | None]:
    pending_review = state["pending_exploration_review"]
    if pending_review is None:
        raise ValueError("pending exploration review must be present before applying review decision")
    if pending_review.decision is None:
        raise ValueError("pending exploration review is still awaiting decision")
    artifact = deps.apply_pending_exploration_review(pending_review)
    return {
        "submitted_steps": [*state["submitted_steps"], artifact],
        "pending_exploration_review": None,
    }


def advance_batch_node(
    state: OrchestrationState,
    deps: OrchestratorRuntimeDeps,
) -> dict[str, ReportingExecutionContext | ActiveEvidenceBatchContract | list[SubmittedStepArtifact] | PendingExplorationReview | int | None]:
    execution_context = state["execution_context"]
    active_batch = state["active_batch"]
    if execution_context is None or active_batch is None:
        raise ValueError("execution context and active batch must be present before advancing")

    advance_response = deps.advance_batch(
        state["incident"],
        execution_context,
        submitted_steps=state["submitted_steps"],
        batch_id=active_batch.batch_id,
    )
    return {
        "execution_context": advance_response.execution_context,
        "active_batch": None,
        "submitted_steps": [],
        "pending_exploration_review": None,
        "remaining_batch_budget": state["remaining_batch_budget"] - 1,
    }


def render_report_node(
    state: OrchestrationState,
    deps: OrchestratorRuntimeDeps,
) -> dict[str, InvestigationReport]:
    execution_context = state["execution_context"]
    if execution_context is None:
        raise ValueError("execution context must be initialized before rendering")

    return {
        "final_report": deps.render_report(
            state["report_request"],
            execution_context,
        )
    }
