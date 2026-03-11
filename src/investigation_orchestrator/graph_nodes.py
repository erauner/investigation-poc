from dataclasses import dataclass
from typing import Callable

from investigation_service.models import (
    ActiveEvidenceBatchContract,
    AdvanceInvestigationRuntimeResponse,
    EvidenceStepContract,
    ExplorationOutcome,
    InvestigationPlan,
    InvestigationReport,
    InvestigationReportRequest,
    ReportingExecutionContext,
    SubmittedStepArtifact,
)

from .evidence_runner import AppliedExplorationReviewResult, ExternalStepCollectionResult
from .state import OrchestrationState, PendingExplorationReview


@dataclass(frozen=True)
class OrchestratorRuntimeDeps:
    seed_context: Callable[..., ReportingExecutionContext]
    get_active_batch: Callable[..., ActiveEvidenceBatchContract | None]
    advance_batch: Callable[..., AdvanceInvestigationRuntimeResponse]
    render_report: Callable[[InvestigationReportRequest, ReportingExecutionContext], InvestigationReport]
    collect_external_steps: Callable[..., ExternalStepCollectionResult]
    apply_pending_exploration_review: Callable[[PendingExplorationReview], AppliedExplorationReviewResult]
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
) -> dict[str, list[SubmittedStepArtifact] | list[ExplorationOutcome] | PendingExplorationReview | list[EvidenceStepContract] | None]:
    active_batch = state["active_batch"]
    if active_batch is None:
        raise ValueError("active batch must be present before collecting external steps")

    if state["deferred_external_steps"]:
        collection_result = deps.collect_external_steps(
            active_batch,
            steps=state["deferred_external_steps"],
        )
    else:
        collection_result = deps.collect_external_steps(active_batch)
    submitted_steps = [*state["submitted_steps"], *collection_result.submitted_steps]
    exploration_outcomes = [*state.get("exploration_outcomes", []), *collection_result.exploration_outcomes]
    # Workload/service/node transport may record peer-attempt metadata and let
    # planner-owned bounded fallback execute for the same external-preferred step.
    if (
        any(step.execution_mode == "external_preferred" for step in (state["deferred_external_steps"] or active_batch.steps))
        and not collection_result.submitted_steps
        and collection_result.pending_exploration_review is None
    ):
        raise ValueError("required external steps were not materialized")

    return {
        "submitted_steps": submitted_steps,
        "exploration_outcomes": exploration_outcomes,
        "pending_exploration_review": collection_result.pending_exploration_review,
        "deferred_external_steps": list(collection_result.deferred_external_steps),
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
) -> dict[str, list[SubmittedStepArtifact] | list[ExplorationOutcome] | PendingExplorationReview | list[EvidenceStepContract] | None]:
    pending_review = state["pending_exploration_review"]
    if pending_review is None:
        raise ValueError("pending exploration review must be present before applying review decision")
    if pending_review.decision is None:
        raise ValueError("pending exploration review is still awaiting decision")
    review_result = deps.apply_pending_exploration_review(pending_review)
    exploration_outcomes = list(state.get("exploration_outcomes", []))
    if review_result.exploration_outcome is not None:
        exploration_outcomes.append(review_result.exploration_outcome)
    return {
        "submitted_steps": [*state["submitted_steps"], review_result.submitted_step],
        "exploration_outcomes": exploration_outcomes,
        "pending_exploration_review": None,
    }


def advance_batch_node(
    state: OrchestrationState,
    deps: OrchestratorRuntimeDeps,
) -> dict[
    str,
    ReportingExecutionContext
    | ActiveEvidenceBatchContract
    | list[SubmittedStepArtifact]
    | list[ExplorationOutcome]
    | list[EvidenceStepContract]
    | PendingExplorationReview
    | int
    | None,
]:
    execution_context = state["execution_context"]
    active_batch = state["active_batch"]
    if execution_context is None or active_batch is None:
        raise ValueError("execution context and active batch must be present before advancing")

    advance_kwargs = {
        "submitted_steps": state["submitted_steps"],
        "batch_id": active_batch.batch_id,
    }
    if state.get("exploration_outcomes"):
        advance_kwargs["exploration_outcomes"] = state.get("exploration_outcomes", [])
    advance_response = deps.advance_batch(
        state["incident"],
        execution_context,
        **advance_kwargs,
    )
    return {
        "execution_context": advance_response.execution_context,
        "active_batch": None,
        "submitted_steps": [],
        "exploration_outcomes": [],
        "pending_exploration_review": None,
        "deferred_external_steps": [],
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
