from typing import Literal

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from investigation_service.models import (
    ActiveEvidenceBatchContract,
    BuildInvestigationPlanRequest,
    EvidenceStepContract,
    InvestigationReport,
    InvestigationReportRequest,
    ReportingExecutionContext,
    SubmittedStepArtifact,
)


class PendingExplorationReview(BaseModel):
    batch_id: str
    step: EvidenceStepContract
    capability: str
    baseline_artifact: SubmittedStepArtifact
    baseline_runtime_pod_name: str
    adequacy_outcome: Literal["adequate", "weak", "contradictory", "blocked", "not_applicable"]
    adequacy_reasons: list[str] = Field(default_factory=list)
    proposed_probe: str
    decision: Literal["approve", "skip"] | None = None


class OrchestrationState(TypedDict):
    report_request: InvestigationReportRequest
    incident: BuildInvestigationPlanRequest
    execution_context: ReportingExecutionContext | None
    active_batch: ActiveEvidenceBatchContract | None
    submitted_steps: list[SubmittedStepArtifact]
    pending_exploration_review: PendingExplorationReview | None
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
        pending_exploration_review=None,
        remaining_batch_budget=remaining_batch_budget,
        final_report=None,
    )
