from .models import (
    ActiveEvidenceBatchContract,
    AdvanceInvestigationRuntimeResponse,
    BuildInvestigationPlanRequest,
    InvestigationPlan,
    InvestigationReport,
    InvestigationReportingRequest,
    ReportingExecutionContext,
    SubmittedStepArtifact,
)
from .planner import advance_active_evidence_batch, get_active_evidence_batch_contract, PlannerDeps
from .reporting import _planner_deps, render_investigation_report


def seed_execution_context(
    incident: BuildInvestigationPlanRequest,
    *,
    allow_bounded_fallback_execution: bool = False,
    initial_plan: InvestigationPlan | None = None,
) -> ReportingExecutionContext:
    plan = initial_plan or build_plan(incident)
    return ReportingExecutionContext(
        initial_plan=plan,
        updated_plan=plan,
        executions=[],
        allow_bounded_fallback_execution=allow_bounded_fallback_execution,
    )


def build_plan(incident: BuildInvestigationPlanRequest) -> InvestigationPlan:
    from .reporting import build_investigation_plan

    return build_investigation_plan(incident)


def get_active_batch(
    incident: BuildInvestigationPlanRequest,
    execution_context: ReportingExecutionContext,
    *,
    batch_id: str | None = None,
) -> ActiveEvidenceBatchContract | None:
    plan = execution_context.updated_plan
    if plan.active_batch_id is None:
        return None
    return get_active_evidence_batch_contract(
        req={
            "plan": plan,
            "incident": incident,
            "batch_id": batch_id or plan.active_batch_id,
        }
    )


def advance_batch(
    incident: BuildInvestigationPlanRequest,
    execution_context: ReportingExecutionContext,
    *,
    submitted_steps: list[SubmittedStepArtifact],
    batch_id: str | None = None,
    deps: PlannerDeps | None = None,
) -> AdvanceInvestigationRuntimeResponse:
    result = advance_active_evidence_batch(
        plan=execution_context.updated_plan,
        incident=incident,
        submitted_steps=submitted_steps,
        batch_id=batch_id,
        deps=deps or _planner_deps(),
    )
    updated_context = ReportingExecutionContext(
        initial_plan=execution_context.initial_plan or execution_context.updated_plan,
        updated_plan=result.updated_plan,
        executions=[*execution_context.executions, result.execution],
        allow_bounded_fallback_execution=execution_context.allow_bounded_fallback_execution,
    )
    return AdvanceInvestigationRuntimeResponse(
        execution_context=updated_context,
        next_active_batch=get_active_batch(
            incident,
            updated_context,
            batch_id=result.updated_plan.active_batch_id,
        ),
    )


def render_report(
    incident: BuildInvestigationPlanRequest,
    execution_context: ReportingExecutionContext,
) -> InvestigationReport:
    return render_investigation_report(
        InvestigationReportingRequest(
            **incident.model_dump(mode="python"),
            execution_context=execution_context,
        )
    )
