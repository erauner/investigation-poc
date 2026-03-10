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

from .state import OrchestrationState


@dataclass(frozen=True)
class OrchestratorRuntimeDeps:
    seed_context: Callable[..., ReportingExecutionContext]
    get_active_batch: Callable[..., ActiveEvidenceBatchContract | None]
    advance_batch: Callable[..., AdvanceInvestigationRuntimeResponse]
    render_report: Callable[[InvestigationReportRequest, ReportingExecutionContext], InvestigationReport]
    run_required_external_steps: Callable[[ActiveEvidenceBatchContract], list[SubmittedStepArtifact]]
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


def run_external_steps_node(
    state: OrchestrationState,
    deps: OrchestratorRuntimeDeps,
) -> dict[str, list[SubmittedStepArtifact]]:
    active_batch = state["active_batch"]
    if active_batch is None:
        raise ValueError("active batch must be present before collecting external steps")

    submitted_steps = deps.run_required_external_steps(active_batch)
    if any(step.execution_mode == "external_preferred" for step in active_batch.steps) and not submitted_steps:
        raise ValueError("required external steps were not materialized")

    return {"submitted_steps": submitted_steps}


def advance_batch_node(
    state: OrchestrationState,
    deps: OrchestratorRuntimeDeps,
) -> dict[str, ReportingExecutionContext | ActiveEvidenceBatchContract | list[SubmittedStepArtifact] | int | None]:
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
