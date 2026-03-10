from typing_extensions import TypedDict

from investigation_service.models import (
    ActiveEvidenceBatchContract,
    BuildInvestigationPlanRequest,
    InvestigationReport,
    InvestigationReportRequest,
    ReportingExecutionContext,
    SubmittedStepArtifact,
)


class OrchestrationState(TypedDict):
    report_request: InvestigationReportRequest
    incident: BuildInvestigationPlanRequest
    execution_context: ReportingExecutionContext | None
    active_batch: ActiveEvidenceBatchContract | None
    submitted_steps: list[SubmittedStepArtifact]
    remaining_batch_budget: int
    final_report: InvestigationReport | None


def build_initial_state(
    report_request: InvestigationReportRequest,
    incident: BuildInvestigationPlanRequest,
    *,
    remaining_batch_budget: int,
) -> OrchestrationState:
    return OrchestrationState(
        report_request=report_request,
        incident=incident,
        execution_context=None,
        active_batch=None,
        submitted_steps=[],
        remaining_batch_budget=remaining_batch_budget,
        final_report=None,
    )
