from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .models import (
    BuildInvestigationPlanRequest,
    CollectAlertContextRequest,
    CollectContextRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    EvidenceBatch,
    FindUnhealthyPodRequest,
    InvestigationPlan,
    InvestigationTarget,
    InvestigationMode,
    InvestigationReportRequest,
    NormalizedInvestigationRequest,
    PlanStep,
    TargetRef,
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


def normalized_request_from_target(target: InvestigationTarget) -> NormalizedInvestigationRequest:
    return NormalizedInvestigationRequest(
        source=target.source,
        scope=target.scope,
        cluster=target.cluster,
        namespace=target.namespace,
        target=target.target,
        node_name=target.node_name,
        service_name=target.service_name,
        profile=target.profile,
        lookback_minutes=target.lookback_minutes,
        normalization_notes=list(target.normalization_notes),
    )


def investigation_target_from_context_request(req: CollectContextRequest) -> InvestigationTarget:
    scope = req.target.split("/", 1)[0] if "/" in req.target else req.profile
    node_name = req.target.split("/", 1)[1] if scope == "node" and "/" in req.target else None
    service_name = req.service_name or (req.target.split("/", 1)[1] if scope == "service" and "/" in req.target else None)
    return InvestigationTarget(
        source="manual",
        scope=scope,
        cluster=req.cluster,
        namespace=req.namespace,
        requested_target=req.target,
        target=req.target,
        node_name=node_name,
        service_name=service_name,
        profile=req.profile,
        lookback_minutes=req.lookback_minutes,
        normalization_notes=[],
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
        service_name=(req.service_name or target.split("/", 1)[1]) if scope == "service" and "/" in target else None,
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


def collect_context_for_normalized_request(
    normalized: NormalizedInvestigationRequest,
    deps: PlannerDeps,
):
    if normalized.scope == "node":
        return deps.collect_node_context(
            CollectNodeContextRequest(
                cluster=normalized.cluster,
                node_name=normalized.node_name or normalized.target.split("/", 1)[1],
                lookback_minutes=normalized.lookback_minutes,
            )
        )
    if normalized.scope == "service":
        if not normalized.namespace:
            raise ValueError("namespace is required for service investigations")
        service_name = normalized.service_name or normalized.target.split("/", 1)[1]
        return deps.collect_service_context(
            CollectServiceContextRequest(
                cluster=normalized.cluster,
                namespace=normalized.namespace,
                service_name=service_name,
                target=normalized.target,
                lookback_minutes=normalized.lookback_minutes,
            )
        )
    return deps.collect_workload_context(
        CollectContextRequest(
            cluster=normalized.cluster,
            namespace=normalized.namespace,
            target=normalized.target,
            profile=normalized.profile,
            service_name=normalized.service_name,
            lookback_minutes=normalized.lookback_minutes,
        )
    )


def align_normalized_request_with_context(
    normalized: NormalizedInvestigationRequest,
    context,
) -> NormalizedInvestigationRequest:
    target_ref = getattr(context, "target", None)
    target_kind = getattr(target_ref, "kind", None)
    if target_kind == "pod" and normalized.target.startswith("pod/") and normalized.target != f"pod/{target_ref.name}":
        notes = list(normalized.normalization_notes)
        notes.append(f"resolved pod target to {target_ref.name}")
        return normalized.model_copy(
            update={
                "target": f"pod/{target_ref.name}",
                "normalization_notes": notes,
            }
        )
    if target_kind != "service" or normalized.scope == "service":
        return normalized

    notes = list(normalized.normalization_notes)
    notes.append(f"profile promoted to service after resolving target kind={target_kind}")
    return normalized.model_copy(
        update={
            "scope": "service",
            "profile": "service",
            "target": f"service/{target_ref.name}",
            "service_name": normalized.service_name or target_ref.name,
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
        planning_notes=notes,
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
