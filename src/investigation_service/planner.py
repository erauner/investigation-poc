from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .adequacy import assess_target_evidence_adequacy
from .execution_policy import policy_fields
from .ingress import (
    IngressDeps,
    ingress_request_from_report_request,
    normalize_ingress_request,
    subject_context_from_subject_set,
)
from .models import (
    ActiveEvidenceBatchContract,
    ActualRoute,
    BuildInvestigationPlanRequest,
    CollectAlertContextRequest,
    CollectCorrelatedChangesRequest,
    CollectContextRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    CorrelatedChangesResponse,
    EvidenceBatch,
    EvidenceBatchExecution,
    EvidenceBundle,
    EvidenceStepContract,
    ExecuteInvestigationStepRequest,
    FindUnhealthyPodRequest,
    GetActiveEvidenceBatchRequest,
    InvestigationSubject,
    InvestigationPlan,
    InvestigationPlannerSeed,
    InvestigationTarget,
    InvestigationMode,
    InvestigationReportRequest,
    NormalizedInvestigationRequest,
    NormalizedInvestigationSubjectSet,
    PlanStep,
    StepExecutionInputs,
    StepRouteProvenance,
    StepArtifact,
    SubmitEvidenceArtifactsRequest,
    SubmittedEvidenceReconciliationResult,
    SubmittedStepArtifact,
    UpdateInvestigationPlanRequest,
)
from .planner_seed import (
    PostSeedNormalizationDeps,
    apply_post_seed_normalization,
    PlannerSeedDeps,
    normalized_request_from_planner_seed,
    planner_seed_from_subject_set,
)


@dataclass(frozen=True)
class PlannerDeps:
    normalize_alert_input: Callable[[CollectAlertContextRequest], NormalizedInvestigationRequest]
    canonical_target: Callable[[str, str, str | None], str]
    scope_from_target: Callable[[str, str], str]
    resolve_cluster: Callable[[str | None], Any]
    get_backend_cr: Callable[..., dict]
    get_frontend_cr: Callable[..., dict]
    get_cluster_cr: Callable[..., dict]
    find_unhealthy_pod: Callable[[FindUnhealthyPodRequest], Any]
    collect_alert_evidence: Callable[[CollectAlertContextRequest], EvidenceBundle] = field(
        default=lambda req: (_ for _ in ()).throw(AssertionError("unexpected alert evidence collection"))
    )
    collect_node_evidence: Callable[[CollectNodeContextRequest], EvidenceBundle] = field(
        default=lambda req: (_ for _ in ()).throw(AssertionError("unexpected node evidence collection"))
    )
    collect_service_evidence: Callable[[CollectServiceContextRequest], EvidenceBundle] = field(
        default=lambda req: (_ for _ in ()).throw(AssertionError("unexpected service evidence collection"))
    )
    collect_workload_evidence: Callable[[CollectContextRequest], EvidenceBundle] = field(
        default=lambda req: (_ for _ in ()).throw(AssertionError("unexpected workload evidence collection"))
    )
    collect_change_candidates: Callable[[CollectCorrelatedChangesRequest], CorrelatedChangesResponse] = field(
        default=lambda req: (_ for _ in ()).throw(AssertionError("unexpected change candidate collection"))
    )


@dataclass(frozen=True)
class StepRuntimeSpec:
    artifact_type: str
    execution_mode: str
    external_submission_allowed: bool


def _ingress_deps(deps: PlannerDeps) -> IngressDeps:
    return IngressDeps(
        resolve_cluster=deps.resolve_cluster,
        get_cluster_cr=deps.get_cluster_cr,
        find_unhealthy_pod=deps.find_unhealthy_pod,
    )


def _planner_seed_deps(deps: PlannerDeps) -> PlannerSeedDeps:
    return PlannerSeedDeps(
        canonical_target=deps.canonical_target,
        scope_from_target=deps.scope_from_target,
        resolve_cluster=deps.resolve_cluster,
        get_backend_cr=deps.get_backend_cr,
        get_frontend_cr=deps.get_frontend_cr,
        get_cluster_cr=deps.get_cluster_cr,
    )


def classify_investigation_mode(
    req: BuildInvestigationPlanRequest | InvestigationReportRequest,
    *,
    has_resolved_target: bool = False,
    has_subject_candidates: bool = False,
    subject_resolution_status: str = "unresolved",
) -> InvestigationMode:
    if req.alertname:
        return "alert_rca"
    if getattr(req, "objective", "auto") == "factual":
        return "factual_analysis"
    if has_resolved_target:
        return "targeted_rca"
    if (
        getattr(req, "question", None)
        and not req.target
        and not has_subject_candidates
        and subject_resolution_status == "unresolved"
    ):
        return "factual_analysis"
    return "targeted_rca"


def investigation_target_from_normalized(
    normalized: NormalizedInvestigationRequest,
    *,
    requested_target: str | None = None,
) -> InvestigationTarget:
    return InvestigationTarget(
        source=normalized.source,
        scope=normalized.scope,
        cluster=normalized.cluster,
        namespace=normalized.namespace,
        requested_target=requested_target or normalized.target,
        target=normalized.target,
        node_name=normalized.node_name,
        service_name=normalized.service_name,
        profile=normalized.profile,
        lookback_minutes=normalized.lookback_minutes,
        normalization_notes=list(normalized.normalization_notes),
        subject_context=normalized.subject_context.model_copy(deep=True) if normalized.subject_context else None,
    )


def normalized_request(
    req: InvestigationReportRequest,
    deps: PlannerDeps,
) -> NormalizedInvestigationRequest:
    _, seed = _subject_set_and_seed(req, deps)
    return _normalized_request_from_seed(seed, deps)


def resolve_primary_target(
    req: InvestigationReportRequest,
    deps: PlannerDeps,
) -> InvestigationTarget:
    _, seed = _subject_set_and_seed(req, deps)
    normalized = _normalized_request_from_seed(seed, deps)
    requested_target = seed.requested_target or normalized.target
    return investigation_target_from_normalized(normalized, requested_target=requested_target)


def _normalized_subject_set(
    req: InvestigationReportRequest,
    deps: PlannerDeps,
):
    ingress_req = ingress_request_from_report_request(req)
    return normalize_ingress_request(ingress_req, _ingress_deps(deps))


def _subject_set_and_seed(
    req: InvestigationReportRequest,
    deps: PlannerDeps,
) -> tuple[NormalizedInvestigationSubjectSet, InvestigationPlannerSeed]:
    subject_set = _normalized_subject_set(req, deps)
    subject_context = subject_context_from_subject_set(subject_set)
    seed = planner_seed_from_subject_set(
        subject_set,
        subject_context=subject_context,
        deps=_planner_seed_deps(deps),
    )
    return subject_set, seed


def _seed_to_normalized_or_none(
    seed: InvestigationPlannerSeed,
    deps: PlannerDeps,
) -> NormalizedInvestigationRequest | None:
    try:
        return _normalized_request_from_seed(seed, deps)
    except ValueError as exc:
        message = str(exc)
        if message.startswith("bounded ingress ambiguity:") or message == (
            "no canonical investigation subject could be resolved from ingress input"
        ):
            return None
        raise


def _normalized_request_from_seed(
    seed: InvestigationPlannerSeed,
    deps: PlannerDeps,
) -> NormalizedInvestigationRequest:
    normalized = normalized_request_from_planner_seed(seed)
    return apply_post_seed_normalization(
        normalized,
        PostSeedNormalizationDeps(find_unhealthy_pod=deps.find_unhealthy_pod),
    )


def _report_request_from_plan_request(req: BuildInvestigationPlanRequest) -> InvestigationReportRequest:
    return InvestigationReportRequest(
        cluster=req.cluster,
        namespace=req.namespace,
        target=req.target,
        question=req.question,
        profile=req.profile,
        service_name=req.service_name,
        lookback_minutes=req.lookback_minutes,
        alertname=req.alertname,
        labels=req.labels,
        annotations=req.annotations,
        node_name=req.node_name,
    )


def _step_map(plan: InvestigationPlan) -> dict[str, PlanStep]:
    return {step.id: step for step in plan.steps}


def _batch_map(plan: InvestigationPlan) -> dict[str, EvidenceBatch]:
    return {batch.id: batch for batch in plan.evidence_batches}


def _batch_for_step(plan: InvestigationPlan, step_id: str) -> EvidenceBatch | None:
    return next((batch for batch in plan.evidence_batches if step_id in batch.step_ids), None)


def _dependencies_satisfied(step: PlanStep, completed_step_ids: set[str]) -> bool:
    return all(dep in completed_step_ids for dep in step.depends_on)


def _summary_for_evidence_bundle(bundle: EvidenceBundle) -> list[str]:
    if bundle.findings:
        return [finding.title for finding in bundle.findings[:3]]
    return [f"Collected {bundle.target.kind} evidence for {bundle.target.name}"]


def _summary_for_alert_bundle(
    alertname: str,
    requested_target: str | None,
    bundle: EvidenceBundle,
) -> list[str]:
    values: list[str] = []
    if requested_target:
        values.append(f"Alert {alertname} requested {requested_target}")
    values.append(f"Resolved runtime target: {bundle.target.kind}/{bundle.target.name}")
    values.extend(_summary_for_evidence_bundle(bundle))
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _summary_for_change_candidates(changes: CorrelatedChangesResponse) -> list[str]:
    if changes.changes:
        return [change.summary for change in changes.changes[:3]]
    return ["No meaningful change candidates found in the requested window"]


def _target_collect_request(target: InvestigationTarget) -> CollectNodeContextRequest | CollectServiceContextRequest | CollectContextRequest:
    if target.scope == "node":
        return CollectNodeContextRequest(
            cluster=target.cluster,
            node_name=target.node_name or target.target.split("/", 1)[1],
            lookback_minutes=target.lookback_minutes,
        )
    if target.scope == "service":
        if not target.namespace:
            raise ValueError("namespace is required for service investigations")
        return CollectServiceContextRequest(
            cluster=target.cluster,
            namespace=target.namespace,
            service_name=target.service_name or target.target.split("/", 1)[1],
            target=target.target,
            lookback_minutes=target.lookback_minutes,
        )
    return CollectContextRequest(
        cluster=target.cluster,
        namespace=target.namespace,
        target=target.target,
        profile=target.profile,
        service_name=target.service_name,
        lookback_minutes=target.lookback_minutes,
    )


def _change_candidates_request(target: InvestigationTarget) -> CollectCorrelatedChangesRequest:
    return CollectCorrelatedChangesRequest(
        cluster=target.cluster,
        namespace=target.namespace,
        target=target.target,
        profile=target.profile,
        service_name=target.service_name,
        lookback_minutes=max(target.lookback_minutes, 60),
    )


def _actual_route(
    step: PlanStep,
    *,
    target: InvestigationTarget,
) -> ActualRoute:
    tool_name = None
    tool_path = ["planner._execute_step"]

    if step.id == "collect-alert-evidence":
        tool_name = "collect_alert_evidence"
    elif step.id == "collect-target-evidence":
        if target.scope == "node":
            tool_name = "collect_node_evidence"
        elif target.scope == "service":
            tool_name = "collect_service_evidence"
        else:
            tool_name = "collect_workload_evidence"
    elif step.id == "collect-service-follow-up-evidence":
        tool_name = "collect_service_evidence"
    elif step.id == "collect-change-candidates":
        tool_name = "collect_change_candidates"

    if tool_name is not None:
        tool_path.append(f"deps.{tool_name}")

    return ActualRoute(
        source_kind="investigation_internal",
        mcp_server="investigation-mcp-server",
        tool_name=tool_name,
        tool_path=tool_path,
    )


def _route_satisfaction(step: PlanStep, actual_route: ActualRoute) -> str:
    if not step.suggested_capability:
        return "not_applicable"

    if (
        step.preferred_mcp_server is None
        and not step.preferred_tool_names
        and step.fallback_mcp_server is None
        and not step.fallback_tool_names
    ):
        return "not_applicable"

    if (
        actual_route.mcp_server == step.preferred_mcp_server
        and actual_route.tool_name in step.preferred_tool_names
    ):
        return "preferred"

    if (
        actual_route.mcp_server == step.fallback_mcp_server
        and actual_route.tool_name in step.fallback_tool_names
    ):
        return "fallback"

    return "unmatched"


def _route_provenance(step: PlanStep, *, target: InvestigationTarget) -> StepRouteProvenance:
    actual_route = _actual_route(step, target=target)
    return StepRouteProvenance(
        requested_capability=step.suggested_capability,
        route_satisfaction=_route_satisfaction(step, actual_route),
        actual_route=actual_route,
    )


def _runtime_spec(step: PlanStep) -> StepRuntimeSpec:
    if step.id == "collect-change-candidates":
        return StepRuntimeSpec(
            artifact_type="change_candidates",
            execution_mode="control_plane_only",
            external_submission_allowed=False,
        )
    if step.id == "collect-alert-evidence":
        return StepRuntimeSpec(
            artifact_type="evidence_bundle",
            execution_mode="control_plane_only",
            external_submission_allowed=False,
        )
    if step.suggested_capability in {"workload_evidence_plane", "service_evidence_plane", "node_evidence_plane"}:
        return StepRuntimeSpec(
            artifact_type="evidence_bundle",
            execution_mode="external_preferred",
            external_submission_allowed=True,
        )
    return StepRuntimeSpec(
        artifact_type="evidence_bundle",
        execution_mode="control_plane_only",
        external_submission_allowed=True,
    )


def _artifact_type_for_step(step: PlanStep) -> str:
    return _runtime_spec(step).artifact_type


def _execution_mode_for_step(step: PlanStep) -> str:
    return _runtime_spec(step).execution_mode


def _pending_batch_steps(plan: InvestigationPlan, batch: EvidenceBatch) -> list[PlanStep]:
    steps = _step_map(plan)
    return [steps[step_id] for step_id in batch.step_ids if steps[step_id].status != "completed"]


def _subject_from_incident(
    incident: BuildInvestigationPlanRequest,
    target: InvestigationTarget | None,
) -> InvestigationSubject:
    if incident.alertname:
        summary = incident.question or f"Investigate alert {incident.alertname}"
        return InvestigationSubject(
            source="alert",
            kind="alert",
            summary=summary,
            requested_target=target.requested_target if target else incident.target,
            alertname=incident.alertname,
        )
    if incident.target:
        return InvestigationSubject(
            source="manual",
            kind="target",
            summary=incident.question or f"Investigate {incident.target}",
            requested_target=incident.target,
        )
    return InvestigationSubject(
        source="manual",
        kind="question",
        summary=incident.question or "Investigate the reported issue",
        requested_target=target.requested_target if target else None,
    )


def _step_execution_inputs(
    step: PlanStep,
    *,
    target: InvestigationTarget,
    incident: BuildInvestigationPlanRequest,
) -> StepExecutionInputs:
    primary_subject = target.subject_context.primary_subject if target.subject_context else None
    related_subjects = list(target.subject_context.related_subjects) if target.subject_context else []
    if step.id == "collect-alert-evidence":
        return StepExecutionInputs(
            request_kind="alert_context",
            cluster=target.cluster,
            namespace=target.namespace or incident.namespace,
            target=target.target,
            profile=target.profile,
            service_name=target.service_name,
            node_name=target.node_name or incident.node_name,
            lookback_minutes=target.lookback_minutes,
            alertname=incident.alertname,
            labels=dict(incident.labels),
            annotations=dict(incident.annotations),
            primary_subject=primary_subject,
            related_subjects=related_subjects,
        )
    if step.id == "collect-change-candidates":
        request = _change_candidates_request(target)
        return StepExecutionInputs(
            request_kind="change_candidates",
            cluster=request.cluster,
            namespace=request.namespace,
            target=request.target,
            profile=request.profile,
            service_name=request.service_name,
            lookback_minutes=request.lookback_minutes,
            anchor_timestamp=request.anchor_timestamp,
            limit=request.limit,
            primary_subject=primary_subject,
            related_subjects=related_subjects,
        )
    if step.id == "collect-service-follow-up-evidence":
        return StepExecutionInputs(
            request_kind="service_context",
            cluster=target.cluster,
            namespace=target.namespace,
            target=f"service/{target.service_name}" if target.service_name else None,
            profile="service",
            service_name=target.service_name,
            lookback_minutes=target.lookback_minutes,
            primary_subject=primary_subject,
            related_subjects=related_subjects,
        )
    if step.id == "collect-target-evidence" and (
        target.scope == "service"
        or target.profile == "service"
        or step.suggested_capability == "service_evidence_plane"
    ):
        return StepExecutionInputs(
            request_kind="service_context",
            cluster=target.cluster,
            namespace=target.namespace,
            target=target.target,
            profile="service",
            service_name=target.service_name,
            lookback_minutes=target.lookback_minutes,
            primary_subject=primary_subject,
            related_subjects=related_subjects,
        )
    request = _target_collect_request(target)
    return StepExecutionInputs(
        request_kind="target_context",
        cluster=request.cluster,
        namespace=getattr(request, "namespace", None),
        target=getattr(request, "target", None),
        profile=getattr(request, "profile", None),
        service_name=getattr(request, "service_name", None),
        node_name=getattr(request, "node_name", None),
        lookback_minutes=request.lookback_minutes,
        primary_subject=primary_subject,
        related_subjects=related_subjects,
    )


def get_active_evidence_batch_contract(req: GetActiveEvidenceBatchRequest) -> ActiveEvidenceBatchContract:
    batch = select_active_evidence_batch(req.plan, batch_id=req.batch_id)
    target = req.plan.target
    if target is None:
        raise ValueError("investigation plan did not produce a primary target")
    pending_steps = _pending_batch_steps(req.plan, batch)
    return ActiveEvidenceBatchContract(
        batch_id=batch.id,
        title=batch.title,
        intent=batch.intent,
        subject=_subject_from_incident(req.incident, target),
        canonical_target=target,
        steps=[
            EvidenceStepContract(
                step_id=step.id,
                title=step.title,
                plane=step.plane,
                artifact_type=_artifact_type_for_step(step),
                requested_capability=step.suggested_capability,
                preferred_mcp_server=step.preferred_mcp_server,
                preferred_tool_names=list(step.preferred_tool_names),
                fallback_mcp_server=step.fallback_mcp_server,
                fallback_tool_names=list(step.fallback_tool_names),
                execution_mode=_execution_mode_for_step(step),
                exploration_intent="follow_up" if step.id == "collect-service-follow-up-evidence" else None,
                execution_inputs=_step_execution_inputs(step, target=target, incident=req.incident),
            )
            for step in pending_steps
        ],
    )


def _step_artifact_from_submission(
    step: PlanStep,
    *,
    target: InvestigationTarget,
    incident: BuildInvestigationPlanRequest,
    submission: SubmittedStepArtifact,
) -> StepArtifact:
    artifact_type = _artifact_type_for_step(step)
    route_provenance = StepRouteProvenance(
        requested_capability=step.suggested_capability,
        route_satisfaction=_route_satisfaction(step, submission.actual_route),
        actual_route=submission.actual_route,
        contributing_routes=list(submission.contributing_routes),
        attempted_routes=list(submission.attempted_routes),
    )
    if artifact_type == "change_candidates":
        if submission.change_candidates is None:
            raise ValueError(f"step {step.id} requires change_candidates payload")
        changes = submission.change_candidates
        return StepArtifact(
            step_id=step.id,
            plane=step.plane,
            artifact_type="change_candidates",
            summary=list(submission.summary) or _summary_for_change_candidates(changes),
            limitations=list(submission.limitations) or list(changes.limitations),
            change_candidates=changes,
            route_provenance=route_provenance,
        )
    if submission.evidence_bundle is None:
        raise ValueError(f"step {step.id} requires evidence_bundle payload")
    bundle = submission.evidence_bundle
    if step.id == "collect-alert-evidence":
        summary = list(submission.summary) or _summary_for_alert_bundle(incident.alertname, target.requested_target, bundle)
    else:
        summary = list(submission.summary) or _summary_for_evidence_bundle(bundle)
    return StepArtifact(
        step_id=step.id,
        plane=step.plane,
        artifact_type="evidence_bundle",
        summary=summary,
        limitations=list(submission.limitations) or list(bundle.limitations),
        evidence_bundle=bundle,
        route_provenance=route_provenance,
    )


def _attempt_only_peer_submission(
    step: PlanStep,
    submission: SubmittedStepArtifact,
) -> bool:
    if step.suggested_capability not in {"workload_evidence_plane", "service_evidence_plane", "node_evidence_plane"}:
        return False
    allowed_servers = {
        server
        for server in (step.preferred_mcp_server, step.fallback_mcp_server)
        if server
    }
    routes = [submission.actual_route, *submission.attempted_routes]
    return (
        submission.evidence_bundle is None
        and submission.change_candidates is None
        and bool(submission.limitations)
        and submission.actual_route.source_kind == "peer_mcp"
        and bool(submission.actual_route.mcp_server)
        and all(route.source_kind == "peer_mcp" and bool(route.mcp_server) for route in routes)
        and all(route.mcp_server in allowed_servers for route in routes)
    )


def _pending_steps_and_submissions(
    *,
    plan: InvestigationPlan,
    incident: BuildInvestigationPlanRequest,
    batch_id: str | None,
    submitted_steps: list[SubmittedStepArtifact],
) -> tuple[EvidenceBatch, InvestigationTarget, dict[str, PlanStep], dict[str, SubmittedStepArtifact]]:
    if plan.mode == "factual_analysis":
        raise ValueError("external evidence submission is not supported for factual_analysis plans")
    batch = select_active_evidence_batch(plan, batch_id=batch_id)
    target = plan.target
    if target is None:
        raise ValueError("investigation plan did not produce a primary target")
    pending_steps = {step.id: step for step in _pending_batch_steps(plan, batch)}
    submissions_by_step: dict[str, SubmittedStepArtifact] = {}
    for submission in submitted_steps:
        if submission.step_id not in pending_steps:
            raise ValueError(f"submitted step {submission.step_id} is not part of active batch {batch.id}")
        if not _runtime_spec(pending_steps[submission.step_id]).external_submission_allowed:
            raise ValueError(f"submitted step {submission.step_id} is control-plane-only and cannot be submitted externally")
        if submission.step_id in submissions_by_step:
            raise ValueError(f"submitted step {submission.step_id} was provided more than once")
        submissions_by_step[submission.step_id] = submission
    return batch, target, pending_steps, submissions_by_step


def _submitted_artifacts_for_batch(
    *,
    batch: EvidenceBatch,
    target: InvestigationTarget,
    incident: BuildInvestigationPlanRequest,
    pending_steps: dict[str, PlanStep],
    submissions_by_step: dict[str, SubmittedStepArtifact],
) -> list[StepArtifact]:
    if not submissions_by_step:
        raise ValueError("submitted_steps must contain at least one step artifact")
    return [
        _step_artifact_from_submission(
            pending_steps[step_id],
            target=target,
            incident=incident,
            submission=submissions_by_step[step_id],
        )
        for step_id in batch.step_ids
        if step_id in submissions_by_step
    ]


def _execute_steps(
    steps: list[PlanStep],
    *,
    plan: InvestigationPlan,
    incident: BuildInvestigationPlanRequest,
    deps: PlannerDeps,
    attempted_peer_submissions: dict[str, SubmittedStepArtifact] | None = None,
) -> list[StepArtifact]:
    return [
        _execute_step(
            step,
            plan=plan,
            incident=incident,
            deps=deps,
            attempted_peer_submission=(attempted_peer_submissions or {}).get(step.id),
        )
        for step in steps
    ]


def _execution_for_batch(
    batch_id: str,
    artifacts: list[StepArtifact],
    *,
    note: str,
) -> EvidenceBatchExecution:
    return EvidenceBatchExecution(
        batch_id=batch_id,
        executed_step_ids=[artifact.step_id for artifact in artifacts],
        artifacts=artifacts,
        execution_notes=[note],
    )


def submit_evidence_step_artifacts(req: SubmitEvidenceArtifactsRequest) -> SubmittedEvidenceReconciliationResult:
    batch, target, pending_steps, submissions_by_step = _pending_steps_and_submissions(
        plan=req.plan,
        incident=req.incident,
        batch_id=req.batch_id,
        submitted_steps=req.submitted_steps,
    )
    artifacts = _submitted_artifacts_for_batch(
        batch=batch,
        target=target,
        incident=req.incident,
        pending_steps=pending_steps,
        submissions_by_step=submissions_by_step,
    )
    execution = _execution_for_batch(
        batch.id,
        artifacts,
        note=f"reconciled externally submitted evidence for {batch.id}",
    )
    updated_plan = update_investigation_plan(UpdateInvestigationPlanRequest(plan=req.plan, execution=execution))
    return SubmittedEvidenceReconciliationResult(execution=execution, updated_plan=updated_plan)


def advance_active_evidence_batch(
    *,
    plan: InvestigationPlan,
    incident: BuildInvestigationPlanRequest,
    submitted_steps: list[SubmittedStepArtifact],
    batch_id: str | None,
    deps: PlannerDeps,
) -> SubmittedEvidenceReconciliationResult:
    if plan.mode == "factual_analysis":
        raise ValueError("advance_active_evidence_batch is not supported for factual_analysis plans")

    batch = select_active_evidence_batch(plan, batch_id=batch_id)
    pending_steps = _pending_batch_steps(plan, batch)
    pending_by_id = {step.id: step for step in pending_steps}
    submitted_artifacts: list[StepArtifact] = []
    submitted_step_ids: set[str] = set()
    attempted_peer_submissions: dict[str, SubmittedStepArtifact] = {}

    if submitted_steps:
        _, target, _, submissions_by_step = _pending_steps_and_submissions(
            plan=plan,
            incident=incident,
            batch_id=batch.id,
            submitted_steps=submitted_steps,
        )
        for step_id, submission in list(submissions_by_step.items()):
            step = pending_by_id[step_id]
            if _attempt_only_peer_submission(step, submission):
                attempted_peer_submissions[step_id] = submission
                del submissions_by_step[step_id]
        if submissions_by_step:
            submitted_artifacts = _submitted_artifacts_for_batch(
                batch=batch,
                target=target,
                incident=incident,
                pending_steps=pending_by_id,
                submissions_by_step=submissions_by_step,
            )
            submitted_step_ids = set(submissions_by_step.keys())

    remaining_steps = [step for step in pending_steps if step.id not in submitted_step_ids]
    blocked_steps = [
        step.id
        for step in remaining_steps
        if _runtime_spec(step).execution_mode != "control_plane_only"
        and (
            step.suggested_capability not in {"workload_evidence_plane", "service_evidence_plane", "node_evidence_plane"}
            or step.id not in attempted_peer_submissions
        )
    ]
    if blocked_steps:
        blocked = ", ".join(blocked_steps)
        raise ValueError(f"active batch still requires external evidence submission for: {blocked}")

    control_plane_artifacts = _execute_steps(
        remaining_steps,
        plan=plan,
        incident=incident,
        deps=deps,
        attempted_peer_submissions=attempted_peer_submissions,
    )
    artifacts_by_step = {
        artifact.step_id: artifact for artifact in [*submitted_artifacts, *control_plane_artifacts]
    }
    artifacts = [artifacts_by_step[step_id] for step_id in batch.step_ids if step_id in artifacts_by_step]
    execution = _execution_for_batch(
        batch.id,
        artifacts,
        note=f"advanced bounded evidence batch {batch.id}",
    )
    if attempted_peer_submissions:
        execution.execution_notes.extend(
            limitation
            for submission in attempted_peer_submissions.values()
            for limitation in submission.limitations
        )
    updated_plan = update_investigation_plan(UpdateInvestigationPlanRequest(plan=plan, execution=execution))
    return SubmittedEvidenceReconciliationResult(execution=execution, updated_plan=updated_plan)


def _execute_step(
    step: PlanStep,
    *,
    plan: InvestigationPlan,
    incident: BuildInvestigationPlanRequest,
    deps: PlannerDeps,
    attempted_peer_submission: SubmittedStepArtifact | None = None,
) -> StepArtifact:
    target = plan.target
    if target is None:
        raise ValueError("investigation plan did not produce a primary target")
    route_provenance = _route_provenance(step, target=target)
    limitations: list[str] = []
    if attempted_peer_submission is not None:
        route_provenance = route_provenance.model_copy(
            update={
                "attempted_routes": list(attempted_peer_submission.attempted_routes)
                or [attempted_peer_submission.actual_route]
            }
        )
        limitations.extend(attempted_peer_submission.limitations)

    if step.id == "collect-alert-evidence":
        if not incident.alertname:
            raise ValueError("alert evidence execution requires alert-shaped incident input")
        bundle = deps.collect_alert_evidence(
            CollectAlertContextRequest(
                alertname=incident.alertname,
                labels=incident.labels,
                annotations=incident.annotations,
                cluster=target.cluster,
                namespace=target.namespace or incident.namespace,
                node_name=target.node_name or incident.node_name,
                target=target.target,
                profile=target.profile,
                service_name=target.service_name,
                lookback_minutes=target.lookback_minutes,
            )
        )
        bundle_with_limitations = bundle.model_copy(update={"limitations": [*list(bundle.limitations), *limitations]})
        return StepArtifact(
            step_id=step.id,
            plane=step.plane,
            artifact_type="evidence_bundle",
            summary=_summary_for_alert_bundle(incident.alertname, target.requested_target, bundle_with_limitations),
            limitations=list(bundle_with_limitations.limitations),
            evidence_bundle=bundle_with_limitations,
            route_provenance=route_provenance,
        )

    if step.id == "collect-target-evidence":
        request = _target_collect_request(target)
        if target.scope == "node":
            bundle = deps.collect_node_evidence(request)
        elif target.scope == "service":
            bundle = deps.collect_service_evidence(request)
        else:
            bundle = deps.collect_workload_evidence(request)
        bundle_with_limitations = bundle.model_copy(update={"limitations": [*list(bundle.limitations), *limitations]})
        return StepArtifact(
            step_id=step.id,
            plane=step.plane,
            artifact_type="evidence_bundle",
            summary=_summary_for_evidence_bundle(bundle_with_limitations),
            limitations=list(bundle_with_limitations.limitations),
            evidence_bundle=bundle_with_limitations,
            route_provenance=route_provenance,
        )

    if step.id == "collect-service-follow-up-evidence":
        if not target.namespace or not target.service_name:
            raise ValueError("service follow-up evidence requires a namespaced service target")
        bundle = deps.collect_service_evidence(
            CollectServiceContextRequest(
                cluster=target.cluster,
                namespace=target.namespace,
                service_name=target.service_name,
                target=f"service/{target.service_name}",
                lookback_minutes=target.lookback_minutes,
            )
        )
        bundle_with_limitations = bundle.model_copy(update={"limitations": [*list(bundle.limitations), *limitations]})
        return StepArtifact(
            step_id=step.id,
            plane=step.plane,
            artifact_type="evidence_bundle",
            summary=_summary_for_evidence_bundle(bundle_with_limitations),
            limitations=list(bundle_with_limitations.limitations),
            evidence_bundle=bundle_with_limitations,
            route_provenance=route_provenance,
        )

    if step.id == "collect-change-candidates":
        changes = deps.collect_change_candidates(_change_candidates_request(target))
        return StepArtifact(
            step_id=step.id,
            plane=step.plane,
            artifact_type="change_candidates",
            summary=_summary_for_change_candidates(changes),
            limitations=list(changes.limitations),
            change_candidates=changes,
            route_provenance=route_provenance,
        )

    raise ValueError(f"unsupported executable plan step: {step.id}")


def select_active_evidence_batch(plan: InvestigationPlan, *, batch_id: str | None = None) -> EvidenceBatch:
    batches = _batch_map(plan)
    batch = batches.get(batch_id or plan.active_batch_id or "")
    if batch is None:
        batch = next((item for item in plan.evidence_batches if item.status == "pending"), None)
    if batch is None:
        raise ValueError("no pending evidence batch is available for execution")

    steps = _step_map(plan)
    if not batch.step_ids:
        raise ValueError(f"batch {batch.id} has no steps to execute")
    resolved_steps = [steps[step_id] for step_id in batch.step_ids]
    if any(step.category != "evidence" for step in resolved_steps):
        raise ValueError(f"batch {batch.id} is not an executable evidence batch")
    return batch


def _primary_evidence_step(target: InvestigationTarget) -> PlanStep:
    if target.scope == "node":
        return PlanStep(
            id="collect-target-evidence",
            title="Collect node evidence",
            category="evidence",
            plane="node",
            rationale="Gather current node state, events, logs, and metrics for the resolved node target.",
            suggested_capability="node_evidence_plane",
            **policy_fields("node_evidence_plane"),
        )
    if target.scope == "service":
        return PlanStep(
            id="collect-target-evidence",
            title="Collect service evidence",
            category="evidence",
            plane="service",
            rationale="Gather service-scoped state, metrics, and recent signals for the resolved service target.",
            suggested_capability="service_evidence_plane",
            **policy_fields("service_evidence_plane"),
        )
    return PlanStep(
        id="collect-target-evidence",
        title="Collect workload evidence",
        category="evidence",
        plane="workload",
        rationale="Gather workload state, events, logs, and metrics for the resolved primary target.",
        suggested_capability="workload_evidence_plane",
        **policy_fields("workload_evidence_plane"),
    )


def _alert_plan(
    req: BuildInvestigationPlanRequest,
    target: InvestigationTarget,
) -> InvestigationPlan:
    steps = [
        PlanStep(
            id="collect-alert-evidence",
            title="Collect alert evidence",
            category="evidence",
            plane="alert",
            rationale="Preserve alert-specific context before drilling into the runtime target.",
            suggested_capability="alert_evidence_plane",
            **policy_fields("alert_evidence_plane"),
        ),
        _primary_evidence_step(target),
        PlanStep(
            id="collect-change-candidates",
            title="Collect change candidates",
            category="evidence",
            plane="changes",
            rationale="Review recent changes around the alert window before forming conclusions.",
            suggested_capability="collect_change_candidates",
            **policy_fields("collect_change_candidates"),
        ),
        PlanStep(
            id="rank-hypotheses",
            title="Rank hypotheses",
            category="analysis",
            plane="analysis",
            status="deferred",
            rationale="Analyze the collected evidence and rank the most plausible explanations.",
            suggested_capability="rank_hypotheses",
            **policy_fields("rank_hypotheses"),
            depends_on=["collect-alert-evidence", "collect-target-evidence", "collect-change-candidates"],
        ),
        PlanStep(
            id="render-report",
            title="Render investigation report",
            category="render",
            plane="report",
            status="deferred",
            rationale="Render the final investigation report after evidence has been gathered and analyzed.",
            suggested_capability="render_investigation_report",
            **policy_fields("render_investigation_report"),
            depends_on=["rank-hypotheses"],
        ),
    ]
    batches = [
        EvidenceBatch(
            id="batch-1",
            title="Initial alert evidence",
            intent="Collect the first bounded evidence batch for alert RCA.",
            step_ids=["collect-alert-evidence", "collect-target-evidence", "collect-change-candidates"],
        ),
        EvidenceBatch(
            id="batch-2",
            title="Rank likely causes",
            status="deferred",
            intent="Analyze the gathered evidence before rendering the final report.",
            step_ids=["rank-hypotheses"],
        ),
        EvidenceBatch(
            id="batch-3",
            title="Render final report",
            status="deferred",
            intent="Render a final report from the ranked hypotheses.",
            step_ids=["render-report"],
        ),
    ]
    return InvestigationPlan(
        mode="alert_rca",
        objective=req.question or f"Investigate alert {req.alertname or target.requested_target}",
        target=target,
        steps=steps,
        evidence_batches=batches,
        active_batch_id="batch-1",
        planning_notes=list(target.normalization_notes),
    )


def _targeted_plan(req: BuildInvestigationPlanRequest, target: InvestigationTarget) -> InvestigationPlan:
    steps = [
        _primary_evidence_step(target),
        PlanStep(
            id="collect-change-candidates",
            title="Collect change candidates",
            category="evidence",
            plane="changes",
            rationale="Review recent changes related to the target before forming conclusions.",
            suggested_capability="collect_change_candidates",
            **policy_fields("collect_change_candidates"),
        ),
        PlanStep(
            id="rank-hypotheses",
            title="Rank hypotheses",
            category="analysis",
            plane="analysis",
            status="deferred",
            rationale="Analyze the gathered evidence and rank the most plausible explanations.",
            suggested_capability="rank_hypotheses",
            **policy_fields("rank_hypotheses"),
            depends_on=["collect-target-evidence", "collect-change-candidates"],
        ),
        PlanStep(
            id="render-report",
            title="Render investigation report",
            category="render",
            plane="report",
            status="deferred",
            rationale="Render the final investigation report after evidence has been gathered and analyzed.",
            suggested_capability="render_investigation_report",
            **policy_fields("render_investigation_report"),
            depends_on=["rank-hypotheses"],
        ),
    ]
    batches = [
        EvidenceBatch(
            id="batch-1",
            title="Initial target evidence",
            intent="Collect the first bounded evidence batch for the resolved target.",
            step_ids=["collect-target-evidence", "collect-change-candidates"],
        ),
        EvidenceBatch(
            id="batch-2",
            title="Rank likely causes",
            status="deferred",
            intent="Analyze the gathered evidence before rendering the final report.",
            step_ids=["rank-hypotheses"],
        ),
        EvidenceBatch(
            id="batch-3",
            title="Render final report",
            status="deferred",
            intent="Render a final report from the ranked hypotheses.",
            step_ids=["render-report"],
        ),
    ]
    return InvestigationPlan(
        mode="targeted_rca",
        objective=req.question or f"Investigate {target.requested_target}",
        target=target,
        steps=steps,
        evidence_batches=batches,
        active_batch_id="batch-1",
        planning_notes=list(target.normalization_notes),
    )


def _factual_plan(
    req: BuildInvestigationPlanRequest,
    target: InvestigationTarget | None,
) -> InvestigationPlan:
    steps = [
        PlanStep(
            id="collect-factual-evidence",
            title="Collect factual evidence",
            category="evidence",
            plane="factual",
            rationale="Gather the primary factual evidence needed to answer the question without defaulting to RCA semantics.",
            suggested_capability=None,
            **policy_fields(None),
        ),
        PlanStep(
            id="summarize-findings",
            title="Summarize findings",
            category="summary",
            plane="summary",
            status="deferred",
            rationale="Summarize the gathered findings once enough factual evidence has been collected.",
            suggested_capability=None,
            **policy_fields(None),
            depends_on=["collect-factual-evidence"],
        ),
    ]
    batches = [
        EvidenceBatch(
            id="batch-1",
            title="Initial factual evidence",
            intent="Collect the first bounded evidence batch needed to answer the factual question.",
            step_ids=["collect-factual-evidence"],
        ),
        EvidenceBatch(
            id="batch-2",
            title="Summarize findings",
            status="deferred",
            intent="Summarize the collected factual evidence.",
            step_ids=["summarize-findings"],
        ),
    ]
    notes = list(target.normalization_notes) if target else []
    if target is None:
        notes.append("factual analysis plan does not require a resolved primary target")
    return InvestigationPlan(
        mode="factual_analysis",
        objective=req.question or req.target or "Answer the factual investigation request",
        target=target,
        steps=steps,
        evidence_batches=batches,
        active_batch_id="batch-1",
        planning_notes=notes,
    )


def execute_investigation_step(
    req: ExecuteInvestigationStepRequest,
    deps: PlannerDeps,
) -> EvidenceBatchExecution:
    if req.plan.mode == "factual_analysis":
        raise ValueError("execute_investigation_step is not supported for factual_analysis plans")

    batch = select_active_evidence_batch(req.plan, batch_id=req.batch_id)
    pending_steps = _pending_batch_steps(req.plan, batch)
    artifacts = _execute_steps(
        pending_steps,
        plan=req.plan,
        incident=req.incident,
        deps=deps,
    )
    return _execution_for_batch(
        batch.id,
        artifacts,
        note=f"executed bounded evidence batch {batch.id}",
    )


def _should_insert_service_follow_up(plan: InvestigationPlan, execution: EvidenceBatchExecution) -> bool:
    target = plan.target
    if target is None or target.scope != "workload" or not target.namespace or not target.service_name:
        return False
    if any(step.id == "collect-service-follow-up-evidence" for step in plan.steps):
        return False

    assessment = assess_target_evidence_adequacy(target=target, artifacts=execution.artifacts)
    return assessment.outcome in {"weak", "contradictory", "blocked"}


def _insert_service_follow_up(plan: InvestigationPlan) -> InvestigationPlan:
    target = plan.target
    if target is None or not target.service_name:
        return plan

    follow_up_step = PlanStep(
        id="collect-service-follow-up-evidence",
        title="Collect service follow-up evidence",
        category="evidence",
        plane="service",
        rationale="Collect one additional bounded service evidence batch before analysis when the primary workload evidence is inconclusive.",
        suggested_capability="service_evidence_plane",
        **policy_fields("service_evidence_plane"),
        depends_on=["collect-target-evidence"],
    )
    follow_up_batch = EvidenceBatch(
        id="batch-follow-up-service",
        title="Service follow-up evidence",
        intent="Collect one additional bounded service evidence batch before analysis.",
        step_ids=[follow_up_step.id],
    )

    updated_steps: list[PlanStep] = []
    for step in plan.steps:
        if step.id == "rank-hypotheses":
            depends_on = list(step.depends_on)
            if follow_up_step.id not in depends_on:
                depends_on.append(follow_up_step.id)
            updated_steps.append(step.model_copy(update={"status": "deferred", "depends_on": depends_on}))
            continue
        updated_steps.append(step)

    updated_batches: list[EvidenceBatch] = []
    inserted = False
    for batch in plan.evidence_batches:
        if batch.id == "batch-2" and not inserted:
            updated_batches.append(follow_up_batch)
            inserted = True
        if batch.id == "batch-2":
            updated_batches.append(batch.model_copy(update={"status": "deferred"}))
            continue
        updated_batches.append(batch)
    if not inserted:
        updated_batches.append(follow_up_batch)

    notes = list(plan.planning_notes)
    notes.append("inserted one bounded service follow-up batch before analysis")
    return plan.model_copy(
        update={
            "steps": updated_steps + [follow_up_step],
            "evidence_batches": updated_batches,
            "active_batch_id": follow_up_batch.id,
            "planning_notes": notes,
        }
    )


def update_investigation_plan(req: UpdateInvestigationPlanRequest) -> InvestigationPlan:
    plan = req.plan
    step_ids = set(req.execution.executed_step_ids)
    batch_id = req.execution.batch_id

    updated_steps: list[PlanStep] = []
    for step in plan.steps:
        if step.id in step_ids:
            updated_steps.append(step.model_copy(update={"status": "completed"}))
            continue
        updated_steps.append(step)

    plan = plan.model_copy(update={"steps": updated_steps, "active_batch_id": None})

    completed_step_ids = {step.id for step in plan.steps if step.status == "completed"}
    refreshed_steps: list[PlanStep] = []
    for step in plan.steps:
        if step.status == "deferred" and _dependencies_satisfied(step, completed_step_ids):
            refreshed_steps.append(step.model_copy(update={"status": "pending"}))
            continue
        refreshed_steps.append(step)

    refreshed_batches: list[EvidenceBatch] = []
    refreshed_step_map = {step.id: step for step in refreshed_steps}
    next_active_batch_id: str | None = None
    for batch in plan.evidence_batches:
        batch_steps = [refreshed_step_map[step_id] for step_id in batch.step_ids]
        if all(step.status == "completed" for step in batch_steps):
            refreshed_batches.append(batch.model_copy(update={"status": "completed"}))
            continue
        if all(step.status in {"completed", "pending"} for step in batch_steps) and any(
            step.status == "pending" for step in batch_steps
        ):
            refreshed_batches.append(batch.model_copy(update={"status": "pending"}))
            if next_active_batch_id is None and all(step.category == "evidence" for step in batch_steps):
                next_active_batch_id = batch.id
            continue
        refreshed_batches.append(batch.model_copy(update={"status": "deferred"}))

    plan = plan.model_copy(update={"steps": refreshed_steps, "evidence_batches": refreshed_batches, "active_batch_id": next_active_batch_id})
    batch_status = next((batch.status for batch in refreshed_batches if batch.id == batch_id), None)
    if batch_status == "completed" and _should_insert_service_follow_up(plan, req.execution):
        return _insert_service_follow_up(plan)

    notes = list(plan.planning_notes)
    notes.append(f"updated plan after executing {batch_id}")
    return plan.model_copy(
        update={
            "steps": refreshed_steps,
            "evidence_batches": refreshed_batches,
            "active_batch_id": next_active_batch_id,
            "planning_notes": notes,
        }
    )


def build_investigation_plan(
    req: BuildInvestigationPlanRequest,
    deps: PlannerDeps,
) -> InvestigationPlan:
    report_req = _report_request_from_plan_request(req)
    subject_set, seed = _subject_set_and_seed(report_req, deps)
    subject_context = seed.subject_context
    normalized = _seed_to_normalized_or_none(seed, deps)
    resolved_target = (
        investigation_target_from_normalized(
            normalized,
            requested_target=seed.requested_target or normalized.target,
        )
        if normalized is not None
        else None
    )
    mode = classify_investigation_mode(
        req,
        has_resolved_target=resolved_target is not None,
        has_subject_candidates=bool(subject_set.candidate_refs),
        subject_resolution_status=subject_context.resolution_status,
    )
    if mode != "factual_analysis" and resolved_target is None and subject_context.resolution_status != "unresolved":
        _normalized_request_from_seed(seed, deps)

    if mode == "alert_rca":
        if resolved_target is None:
            resolved_target = resolve_primary_target(report_req, deps)
        return _alert_plan(req, resolved_target)
    if mode == "targeted_rca":
        if resolved_target is None:
            resolved_target = resolve_primary_target(report_req, deps)
        return _targeted_plan(req, resolved_target)

    return _factual_plan(req, resolved_target)
