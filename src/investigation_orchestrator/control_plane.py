from investigation_service.models import (
    ActiveEvidenceBatchContract,
    AdvanceInvestigationRuntimeRequest,
    AdvanceInvestigationRuntimeResponse,
    BuildInvestigationPlanRequest,
    GetActiveEvidenceBatchRequest,
    InvestigationReport,
    InvestigationReportingRequest,
    InvestigationReportRequest,
    ReportingExecutionContext,
    SubmittedStepArtifact,
)
from investigation_service import reporting


def seed_context(
    incident: BuildInvestigationPlanRequest,
    *,
    allow_bounded_fallback_execution: bool = False,
) -> ReportingExecutionContext:
    plan = reporting.build_investigation_plan(incident)
    return ReportingExecutionContext(
        initial_plan=plan,
        updated_plan=plan,
        executions=[],
        allow_bounded_fallback_execution=allow_bounded_fallback_execution,
    )


def get_active_batch(
    incident: BuildInvestigationPlanRequest,
    execution_context: ReportingExecutionContext,
    *,
    batch_id: str | None = None,
) -> ActiveEvidenceBatchContract | None:
    plan = execution_context.updated_plan
    if plan.active_batch_id is None:
        return None
    return reporting.get_active_evidence_batch(
        GetActiveEvidenceBatchRequest(
            plan=plan,
            incident=incident,
            batch_id=batch_id or plan.active_batch_id,
        )
    )


def advance_batch(
    incident: BuildInvestigationPlanRequest,
    execution_context: ReportingExecutionContext,
    *,
    submitted_steps: list[SubmittedStepArtifact],
    batch_id: str | None = None,
) -> AdvanceInvestigationRuntimeResponse:
    return reporting.advance_investigation_runtime(
        AdvanceInvestigationRuntimeRequest(
            incident=incident,
            execution_context=execution_context,
            submitted_steps=submitted_steps,
            batch_id=batch_id,
        )
    )


def render_report(
    req: InvestigationReportRequest,
    execution_context: ReportingExecutionContext,
) -> InvestigationReport:
    return reporting.render_investigation_report(
        InvestigationReportingRequest(
            **req.model_dump(mode="python"),
            execution_context=execution_context,
        )
    )
