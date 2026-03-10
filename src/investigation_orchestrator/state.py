from dataclasses import dataclass, field

from investigation_service.models import (
    ActiveEvidenceBatchContract,
    BuildInvestigationPlanRequest,
    InvestigationReport,
    ReportingExecutionContext,
    SubmittedStepArtifact,
)


@dataclass
class OrchestrationState:
    incident: BuildInvestigationPlanRequest
    execution_context: ReportingExecutionContext
    active_batch: ActiveEvidenceBatchContract | None = None
    submitted_steps: list[SubmittedStepArtifact] = field(default_factory=list)
    remaining_batch_budget: int = 2
    final_report: InvestigationReport | None = None
