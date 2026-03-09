from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .models import (
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
    ExecuteInvestigationStepRequest,
    FindUnhealthyPodRequest,
    InvestigationPlan,
    InvestigationTarget,
    InvestigationMode,
    InvestigationReportRequest,
    NormalizedInvestigationRequest,
    PlanStep,
    StepArtifact,
    UpdateInvestigationPlanRequest,
)

_VAGUE_WORKLOAD_TARGETS = {
    "pod",
    "pods",
    "workload",
    "workloads",
    "unhealthy",
    "unhealthy-pod",
    "unhealthy-workload",
}


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
    collect_node_context: Callable[[CollectNodeContextRequest], Any]
    collect_service_context: Callable[[CollectServiceContextRequest], Any]
    collect_workload_context: Callable[[CollectContextRequest], Any]
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


def classify_investigation_mode(
    req: BuildInvestigationPlanRequest | InvestigationReportRequest,
) -> InvestigationMode:
    if req.alertname:
        return "alert_rca"
    if getattr(req, "objective", "auto") == "factual":
        return "factual_analysis"
    if getattr(req, "question", None) and not req.target:
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
    )


def normalized_request(
    req: InvestigationReportRequest,
    deps: PlannerDeps,
) -> NormalizedInvestigationRequest:
    if req.alertname:
        return deps.normalize_alert_input(
            CollectAlertContextRequest(
                alertname=req.alertname,
                labels=req.labels,
                annotations=req.annotations,
                cluster=req.cluster,
                namespace=req.namespace,
                node_name=req.node_name,
                target=req.target,
                profile=req.profile,
                service_name=req.service_name,
                lookback_minutes=req.lookback_minutes,
            )
        )

    if not req.target:
        raise ValueError("target is required when alertname is not supplied")

    target = deps.canonical_target(req.target, req.profile, req.service_name)
    scope = deps.scope_from_target(target, req.profile)
    profile = req.profile
    notes = ["target normalized from manual request"]
    if scope == "service" and profile == "workload":
        profile = "service"
        notes.append("profile promoted to service based on target")
    if req.cluster:
        notes.append(f"cluster resolved from explicit: {req.cluster}")

    return NormalizedInvestigationRequest(
        source="manual",
        scope=scope,
        cluster=req.cluster,
        namespace=req.namespace,
        target=target,
        node_name=target.split("/", 1)[1] if scope == "node" and "/" in target else None,
        service_name=(req.service_name or target.split("/", 1)[1]) if scope == "service" and "/" in target else req.service_name,
        profile=profile,
        lookback_minutes=req.lookback_minutes,
        normalization_notes=notes,
    )


def resolve_primary_target(
    req: InvestigationReportRequest,
    deps: PlannerDeps,
) -> InvestigationTarget:
    normalized = normalized_request(req, deps)
    requested_target = normalized.target
    normalized = resolve_backend_convenience_target(normalized, deps)
    normalized = resolve_frontend_convenience_target(normalized, deps)
    normalized = resolve_cluster_convenience_target(normalized, deps)
    normalized = resolve_vague_workload_target(normalized, deps)
    return investigation_target_from_normalized(normalized, requested_target=requested_target)


def resolve_vague_workload_target(
    normalized: NormalizedInvestigationRequest,
    deps: PlannerDeps,
) -> NormalizedInvestigationRequest:
    if normalized.scope != "workload":
        return normalized

    lowered = normalized.target.strip().lower()
    if lowered not in _VAGUE_WORKLOAD_TARGETS:
        return normalized
    if not normalized.namespace:
        raise ValueError("namespace is required when resolving a vague workload target")

    unhealthy = deps.find_unhealthy_pod(
        FindUnhealthyPodRequest(cluster=normalized.cluster, namespace=normalized.namespace)
    )
    candidate = unhealthy.candidate
    if candidate is None:
        raise ValueError("no unhealthy pod found in namespace")

    notes = list(normalized.normalization_notes)
    notes.append(f"resolved vague workload target to {candidate.target}")
    return normalized.model_copy(update={"target": candidate.target, "normalization_notes": notes})


def resolved_cluster_value(cluster) -> str | None:
    if getattr(cluster, "source", None) == "legacy_current_context":
        return None
    return cluster.alias


def resolve_backend_convenience_target(
    normalized: NormalizedInvestigationRequest,
    deps: PlannerDeps,
) -> NormalizedInvestigationRequest:
    raw_target = normalized.target.strip()
    if "/" not in raw_target:
        return normalized

    kind, name = raw_target.split("/", 1)
    if kind.lower() != "backend":
        return normalized
    if not name:
        raise ValueError("backend target name is required")
    if not normalized.namespace:
        raise ValueError("namespace is required for Backend targets")

    cluster = deps.resolve_cluster(normalized.cluster)
    backend = deps.get_backend_cr(normalized.namespace, name, cluster=cluster)
    resolved_target = f"deployment/{name}"
    notes = list(normalized.normalization_notes)
    notes.append(f"resolved Backend/{name} to {resolved_target}")
    if backend.get("error"):
        notes.append("backend lookup failed; using deployment fallback")

    return normalized.model_copy(
        update={
            "cluster": resolved_cluster_value(cluster),
            "scope": "workload",
            "profile": "workload",
            "service_name": name,
            "target": resolved_target,
            "normalization_notes": notes,
        }
    )


def resolve_frontend_convenience_target(
    normalized: NormalizedInvestigationRequest,
    deps: PlannerDeps,
) -> NormalizedInvestigationRequest:
    raw_target = normalized.target.strip()
    if "/" not in raw_target:
        return normalized

    kind, name = raw_target.split("/", 1)
    if kind.lower() != "frontend":
        return normalized
    if not name:
        raise ValueError("frontend target name is required")
    if not normalized.namespace:
        raise ValueError("namespace is required for Frontend targets")

    cluster = deps.resolve_cluster(normalized.cluster)
    frontend = deps.get_frontend_cr(normalized.namespace, name, cluster=cluster)
    if normalized.profile == "service":
        resolved_target = f"service/{name}"
        scope = "service"
        profile = "service"
        service_name = name
    else:
        resolved_target = f"deployment/{name}"
        scope = "workload"
        profile = "workload"
        service_name = name
    notes = list(normalized.normalization_notes)
    notes.append(f"resolved Frontend/{name} to {resolved_target}")
    if frontend.get("error"):
        notes.append(f"frontend lookup failed; using {resolved_target} fallback")

    return normalized.model_copy(
        update={
            "cluster": resolved_cluster_value(cluster),
            "scope": scope,
            "profile": profile,
            "service_name": service_name,
            "target": resolved_target,
            "normalization_notes": notes,
        }
    )


def cluster_component_priority(item: dict) -> tuple[int, int, str]:
    phase = (item.get("phase") or "").lower()
    ready = bool(item.get("ready"))
    if phase == "failed":
        rank = 0
    elif phase == "degraded":
        rank = 1
    elif not ready:
        rank = 2
    elif phase in {"deploying", "waitingfordeps", "pending"}:
        rank = 3
    else:
        rank = 4
    return (rank, int(item.get("wave", 0)), item.get("name") or "")


def component_target(kind: str, name: str, profile: str) -> tuple[str, str, str, str | None]:
    lowered = kind.lower()
    if lowered == "frontend" and profile == "service":
        return (f"service/{name}", "service", "service", name)
    if lowered in {"backend", "frontend"}:
        return (f"deployment/{name}", "workload", "workload", name)
    if lowered == "deployment":
        return (f"deployment/{name}", "workload", "workload", None)
    if lowered == "service":
        return (f"service/{name}", "service", "service", name)
    if lowered == "statefulset":
        return (f"deployment/{name}", "workload", "workload", None)
    raise ValueError(f"unsupported cluster component kind for investigation: {kind}")


def resolve_cluster_convenience_target(
    normalized: NormalizedInvestigationRequest,
    deps: PlannerDeps,
) -> NormalizedInvestigationRequest:
    raw_target = normalized.target.strip()
    if "/" not in raw_target:
        return normalized

    kind, name = raw_target.split("/", 1)
    if kind.lower() != "cluster":
        return normalized
    if not name:
        raise ValueError("cluster target name is required")
    if not normalized.namespace:
        raise ValueError("namespace is required for Cluster targets")

    cluster = deps.resolve_cluster(normalized.cluster)
    cluster_cr = deps.get_cluster_cr(normalized.namespace, name, cluster=cluster)
    if cluster_cr.get("error"):
        raise ValueError(f"cluster lookup failed for {name}: {cluster_cr['error']}")

    statuses = cluster_cr.get("status", {}).get("componentStatuses") or []
    if not statuses:
        raise ValueError(f"cluster {name} has no componentStatuses to investigate")

    selected = sorted(statuses, key=cluster_component_priority)[0]
    component_kind = selected.get("kind") or ""
    component_name = selected.get("name") or ""
    if not component_kind or not component_name:
        raise ValueError(f"cluster {name} has an incomplete component status entry")

    resolved_target, scope, profile, service_name = component_target(
        component_kind, component_name, normalized.profile
    )
    notes = list(normalized.normalization_notes)
    notes.append(f"resolved Cluster/{name} to failing component {component_kind}/{component_name}")
    notes.append(f"resolved {component_kind}/{component_name} to {resolved_target}")

    return normalized.model_copy(
        update={
            "cluster": resolved_cluster_value(cluster),
            "scope": scope,
            "profile": profile,
            "service_name": service_name,
            "target": resolved_target,
            "normalization_notes": notes,
        }
    )


def _report_request_from_plan_request(req: BuildInvestigationPlanRequest) -> InvestigationReportRequest:
    return InvestigationReportRequest(
        cluster=req.cluster,
        namespace=req.namespace,
        target=req.target,
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


def _execute_step(
    step: PlanStep,
    *,
    plan: InvestigationPlan,
    incident: BuildInvestigationPlanRequest,
    deps: PlannerDeps,
) -> StepArtifact:
    target = plan.target
    if target is None:
        raise ValueError("investigation plan did not produce a primary target")

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
        return StepArtifact(
            step_id=step.id,
            plane=step.plane,
            artifact_type="evidence_bundle",
            summary=_summary_for_evidence_bundle(bundle),
            limitations=list(bundle.limitations),
            evidence_bundle=bundle,
        )

    if step.id == "collect-target-evidence":
        request = _target_collect_request(target)
        if target.scope == "node":
            bundle = deps.collect_node_evidence(request)
        elif target.scope == "service":
            bundle = deps.collect_service_evidence(request)
        else:
            bundle = deps.collect_workload_evidence(request)
        return StepArtifact(
            step_id=step.id,
            plane=step.plane,
            artifact_type="evidence_bundle",
            summary=_summary_for_evidence_bundle(bundle),
            limitations=list(bundle.limitations),
            evidence_bundle=bundle,
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
        return StepArtifact(
            step_id=step.id,
            plane=step.plane,
            artifact_type="evidence_bundle",
            summary=_summary_for_evidence_bundle(bundle),
            limitations=list(bundle.limitations),
            evidence_bundle=bundle,
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
            suggested_tool="collect_node_evidence",
        )
    if target.scope == "service":
        return PlanStep(
            id="collect-target-evidence",
            title="Collect service evidence",
            category="evidence",
            plane="service",
            rationale="Gather service-scoped state, metrics, and recent signals for the resolved service target.",
            suggested_tool="collect_service_evidence",
        )
    return PlanStep(
        id="collect-target-evidence",
        title="Collect workload evidence",
        category="evidence",
        plane="workload",
        rationale="Gather workload state, events, logs, and metrics for the resolved primary target.",
        suggested_tool="collect_workload_evidence",
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
            suggested_tool="collect_alert_evidence",
        ),
        _primary_evidence_step(target),
        PlanStep(
            id="collect-change-candidates",
            title="Collect change candidates",
            category="evidence",
            plane="changes",
            rationale="Review recent changes around the alert window before forming conclusions.",
            suggested_tool="collect_change_candidates",
        ),
        PlanStep(
            id="rank-hypotheses",
            title="Rank hypotheses",
            category="analysis",
            plane="analysis",
            status="deferred",
            rationale="Analyze the collected evidence and rank the most plausible explanations.",
            suggested_tool="rank_hypotheses",
            depends_on=["collect-alert-evidence", "collect-target-evidence", "collect-change-candidates"],
        ),
        PlanStep(
            id="render-report",
            title="Render investigation report",
            category="render",
            plane="report",
            status="deferred",
            rationale="Render the final investigation report after evidence has been gathered and analyzed.",
            suggested_tool="render_investigation_report",
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
            suggested_tool="collect_change_candidates",
        ),
        PlanStep(
            id="rank-hypotheses",
            title="Rank hypotheses",
            category="analysis",
            plane="analysis",
            status="deferred",
            rationale="Analyze the gathered evidence and rank the most plausible explanations.",
            suggested_tool="rank_hypotheses",
            depends_on=["collect-target-evidence", "collect-change-candidates"],
        ),
        PlanStep(
            id="render-report",
            title="Render investigation report",
            category="render",
            plane="report",
            status="deferred",
            rationale="Render the final investigation report after evidence has been gathered and analyzed.",
            suggested_tool="render_investigation_report",
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
            suggested_tool=None,
        ),
        PlanStep(
            id="summarize-findings",
            title="Summarize findings",
            category="summary",
            plane="summary",
            status="deferred",
            rationale="Summarize the gathered findings once enough factual evidence has been collected.",
            suggested_tool=None,
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
    steps = _step_map(req.plan)
    artifacts = [
        _execute_step(steps[step_id], plan=req.plan, incident=req.incident, deps=deps)
        for step_id in batch.step_ids
    ]
    return EvidenceBatchExecution(
        batch_id=batch.id,
        executed_step_ids=list(batch.step_ids),
        artifacts=artifacts,
        execution_notes=[f"executed bounded evidence batch {batch.id}"],
    )


def _should_insert_service_follow_up(plan: InvestigationPlan, execution: EvidenceBatchExecution) -> bool:
    target = plan.target
    if target is None or target.scope != "workload" or not target.namespace or not target.service_name:
        return False
    if any(step.id == "collect-service-follow-up-evidence" for step in plan.steps):
        return False

    evidence_artifacts = [artifact for artifact in execution.artifacts if artifact.evidence_bundle is not None]
    if not evidence_artifacts:
        return False

    for artifact in evidence_artifacts:
        bundle = artifact.evidence_bundle
        assert bundle is not None
        if bundle.limitations:
            return True
        if not bundle.findings:
            return True
        if any(finding.title == "No Critical Signals Found" for finding in bundle.findings):
            return True
    return False


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
        suggested_tool="collect_service_evidence",
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

    updated_batches: list[EvidenceBatch] = []
    for batch in plan.evidence_batches:
        if batch.id == batch_id:
            updated_batches.append(batch.model_copy(update={"status": "completed"}))
            continue
        updated_batches.append(batch)

    plan = plan.model_copy(update={"steps": updated_steps, "evidence_batches": updated_batches, "active_batch_id": None})
    if _should_insert_service_follow_up(plan, req.execution):
        return _insert_service_follow_up(plan)

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
        if batch.status == "completed":
            refreshed_batches.append(batch)
            continue
        batch_steps = [refreshed_step_map[step_id] for step_id in batch.step_ids]
        if all(step.status == "completed" for step in batch_steps):
            refreshed_batches.append(batch.model_copy(update={"status": "completed"}))
            continue
        if all(step.status == "pending" for step in batch_steps):
            refreshed_batches.append(batch.model_copy(update={"status": "pending"}))
            if next_active_batch_id is None and all(step.category == "evidence" for step in batch_steps):
                next_active_batch_id = batch.id
            continue
        refreshed_batches.append(batch.model_copy(update={"status": "deferred"}))

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
    mode = classify_investigation_mode(req)
    report_req = _report_request_from_plan_request(req)

    if mode == "alert_rca":
        return _alert_plan(req, resolve_primary_target(report_req, deps))
    if mode == "targeted_rca":
        return _targeted_plan(req, resolve_primary_target(report_req, deps))

    factual_target = resolve_primary_target(report_req, deps) if req.target else None
    return _factual_plan(req, factual_target)
