from .models import (
    ActualRoute,
    ActiveEvidenceBatchContract,
    AdvanceInvestigationRuntimeResponse,
    BuildInvestigationPlanRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    EvidenceStepContract,
    GetActiveEvidenceBatchRequest,
    InvestigationPlan,
    InvestigationReport,
    InvestigationReportRequest,
    InvestigationReportingRequest,
    ReportingExecutionContext,
    SubmittedStepArtifact,
)
from .planner import advance_active_evidence_batch, get_active_evidence_batch_contract, PlannerDeps
from .reporting import _planner_deps, render_investigation_report
from .tools import materialize_node_evidence, materialize_service_evidence, materialize_workload_evidence


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
    req: InvestigationReportRequest,
    execution_context: ReportingExecutionContext,
) -> InvestigationReport:
    return render_investigation_report(
        InvestigationReportingRequest(
            **req.model_dump(mode="python"),
            execution_context=execution_context,
        )
    )


def materialize_workload_submission(
    step: EvidenceStepContract,
    *,
    target,
    object_state: dict,
    events: list[str],
    log_excerpt: str,
    actual_route: ActualRoute,
    cluster_alias: str | None = None,
    extra_limitations: list[str] | None = None,
) -> SubmittedStepArtifact:
    inputs = step.execution_inputs
    bundle = materialize_workload_evidence(
        BuildInvestigationPlanRequest(
            cluster=inputs.cluster,
            namespace=inputs.namespace,
            target=inputs.target or "",
            profile=inputs.profile or "workload",
            service_name=inputs.service_name,
            lookback_minutes=inputs.lookback_minutes or 15,
            alertname=inputs.alertname,
            labels=inputs.labels,
            annotations=inputs.annotations,
            node_name=inputs.node_name,
        ),
        target=target,
        object_state=object_state,
        events=events,
        log_excerpt=log_excerpt,
        cluster_alias=cluster_alias,
        extra_limitations=extra_limitations,
    )
    return SubmittedStepArtifact(
        step_id=step.step_id,
        evidence_bundle=bundle,
        actual_route=actual_route,
    )


def materialize_service_submission(
    step: EvidenceStepContract,
    *,
    target,
    metrics: dict,
    actual_route: ActualRoute,
    object_state: dict | None = None,
    events: list[str] | None = None,
    cluster_alias: str | None = None,
    extra_limitations: list[str] | None = None,
) -> SubmittedStepArtifact:
    inputs = step.execution_inputs
    bundle = materialize_service_evidence(
        CollectServiceContextRequest(
            cluster=inputs.cluster,
            namespace=inputs.namespace or "",
            service_name=inputs.service_name or target.name,
            target=inputs.target,
            lookback_minutes=inputs.lookback_minutes or 15,
        ),
        target=target,
        metrics=metrics,
        object_state=object_state,
        events=events,
        cluster_alias=cluster_alias,
        extra_limitations=extra_limitations,
    )
    return SubmittedStepArtifact(
        step_id=step.step_id,
        evidence_bundle=bundle,
        actual_route=actual_route,
    )


def materialize_node_submission(
    step: EvidenceStepContract,
    *,
    target,
    metrics: dict,
    actual_route: ActualRoute,
    object_state: dict | None = None,
    events: list[str] | None = None,
    cluster_alias: str | None = None,
    extra_limitations: list[str] | None = None,
) -> SubmittedStepArtifact:
    inputs = step.execution_inputs
    bundle = materialize_node_evidence(
        CollectNodeContextRequest(
            cluster=inputs.cluster,
            node_name=inputs.node_name or target.name,
            lookback_minutes=inputs.lookback_minutes or 15,
        ),
        target=target,
        metrics=metrics,
        object_state=object_state,
        events=events,
        cluster_alias=cluster_alias,
        extra_limitations=extra_limitations,
    )
    return SubmittedStepArtifact(
        step_id=step.step_id,
        evidence_bundle=bundle,
        actual_route=actual_route,
    )
