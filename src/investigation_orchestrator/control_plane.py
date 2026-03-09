from investigation_service.models import (
    ActiveEvidenceBatchContract,
    AdvanceInvestigationRuntimeResponse,
    BuildInvestigationPlanRequest,
    InvestigationReport,
    ReportingExecutionContext,
    SubmittedStepArtifact,
)
from investigation_service import runtime_api


def seed_context(
    incident: BuildInvestigationPlanRequest,
    *,
    allow_bounded_fallback_execution: bool = False,
) -> ReportingExecutionContext:
    return runtime_api.seed_execution_context(
        incident,
        allow_bounded_fallback_execution=allow_bounded_fallback_execution,
    )


def get_active_batch(
    incident: BuildInvestigationPlanRequest,
    execution_context: ReportingExecutionContext,
    *,
    batch_id: str | None = None,
) -> ActiveEvidenceBatchContract | None:
    return runtime_api.get_active_batch(incident, execution_context, batch_id=batch_id)


def advance_batch(
    incident: BuildInvestigationPlanRequest,
    execution_context: ReportingExecutionContext,
    *,
    submitted_steps: list[SubmittedStepArtifact],
    batch_id: str | None = None,
) -> AdvanceInvestigationRuntimeResponse:
    return runtime_api.advance_batch(
        incident,
        execution_context,
        submitted_steps=submitted_steps,
        batch_id=batch_id,
    )


def render_report(
    incident: BuildInvestigationPlanRequest,
    execution_context: ReportingExecutionContext,
) -> InvestigationReport:
    return runtime_api.render_report(incident, execution_context)
