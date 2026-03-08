from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .models import (
    CollectAlertContextRequest,
    CollectContextRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    EvidenceBundle,
    FindUnhealthyPodRequest,
    InvestigationTarget,
    InvestigationMode,
    InvestigationReportRequest,
    NormalizedInvestigationRequest,
    PlannedInvestigation,
    TargetRef,
)
from .tools import evidence_bundle_from_context

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


def classify_investigation_mode(req: InvestigationReportRequest) -> InvestigationMode:
    if req.alertname:
        return "alert"
    return "generic"


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


def plan_investigation(
    req: InvestigationReportRequest,
    deps: PlannerDeps,
    *,
    collect_context_for_normalized_request_impl: Callable[[NormalizedInvestigationRequest], Any] | None = None,
    align_normalized_request_with_context_impl: Callable[[NormalizedInvestigationRequest, Any], NormalizedInvestigationRequest]
    | None = None,
) -> PlannedInvestigation:
    collect_context_impl = collect_context_for_normalized_request_impl or (
        lambda normalized: collect_context_for_normalized_request(normalized, deps)
    )
    align_impl = align_normalized_request_with_context_impl or align_normalized_request_with_context

    target = resolve_primary_target(req, deps)
    normalized = normalized_request_from_target(target)
    context = collect_context_impl(normalized)
    normalized = align_impl(normalized, context)
    context_cluster = getattr(context, "cluster", None)
    if context_cluster and not any(note.startswith("cluster resolved") for note in normalized.normalization_notes):
        notes = list(normalized.normalization_notes)
        notes.append(f"cluster resolved from collected context: {context_cluster}")
        normalized = normalized.model_copy(update={"cluster": context_cluster, "normalization_notes": notes})
    target = investigation_target_from_normalized(normalized, requested_target=target.requested_target)
    try:
        evidence = evidence_bundle_from_context(context)
    except AttributeError:
        evidence = EvidenceBundle(
            cluster=getattr(context, "cluster", normalized.cluster or "current-context"),
            target=getattr(context, "target", None)
            or TargetRef(
                namespace=normalized.namespace,
                kind="service" if normalized.scope == "service" else ("node" if normalized.scope == "node" else "pod"),
                name=(normalized.target.split("/", 1)[1] if "/" in normalized.target else normalized.target),
            ),
            object_state=getattr(context, "object_state", {}),
            events=getattr(context, "events", []),
            log_excerpt=getattr(context, "log_excerpt", ""),
            metrics=getattr(context, "metrics", {}),
            findings=getattr(context, "findings", []),
            limitations=getattr(context, "limitations", []),
            enrichment_hints=getattr(context, "enrichment_hints", []),
        )

    return PlannedInvestigation(
        mode=classify_investigation_mode(req),
        target=target,
        evidence=evidence,
        normalized=normalized,
        context=context,
    )
