from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .models import (
    FindUnhealthyPodRequest,
    InvestigationPlannerSeed,
    InvestigationSubjectContext,
    InvestigationSubjectRef,
    NormalizedInvestigationRequest,
    NormalizedInvestigationSubjectSet,
    PlannerSeedExecutionFocus,
)


@dataclass(frozen=True)
class PlannerSeedDeps:
    canonical_target: Callable[[str, str, str | None], str]
    scope_from_target: Callable[[str, str], str]
    resolve_cluster: Callable[..., Any]
    get_backend_cr: Callable[..., dict]
    get_frontend_cr: Callable[..., dict]
    get_cluster_cr: Callable[..., dict]


@dataclass(frozen=True)
class PostSeedNormalizationDeps:
    find_unhealthy_pod: Callable[[FindUnhealthyPodRequest], Any]


def planner_seed_from_subject_set(
    subject_set: NormalizedInvestigationSubjectSet,
    *,
    subject_context: InvestigationSubjectContext,
    deps: PlannerSeedDeps,
) -> InvestigationPlannerSeed:
    seed_notes = list(subject_set.normalization_notes)
    requested_target = _requested_target(subject_set, subject_context)

    seed = InvestigationPlannerSeed(
        source="alert" if subject_set.ingress.alertname else "manual",
        cluster=subject_context.scope.cluster,
        namespace=subject_context.scope.namespace,
        lookback_minutes=subject_set.ingress.lookback_minutes,
        subject_context=subject_context.model_copy(deep=True),
        requested_target=requested_target,
        outcome="bounded_ambiguity" if subject_context.resolution_status != "resolved" else "execution_focus_resolved",
        seed_notes=seed_notes,
    )
    if subject_context.resolution_status != "resolved":
        return seed

    focus = subject_context.primary_subject
    if focus is None:
        raise ValueError("resolved subject context is missing a primary subject")

    focus_notes = list(seed_notes)
    focus_notes.append(f"canonical focus selected: {_subject_ref_string(focus)}")
    if subject_set.related_refs:
        focus_notes.append(
            "related refs preserved: "
            + ", ".join(
                f"{_subject_ref_string(ref)} ({ref.relation})" for ref in subject_set.related_refs
            )
        )

    execution_focus = _execution_focus_for_subject(subject_set, focus, deps, focus_notes)
    return seed.model_copy(
        update={
            "execution_focus": execution_focus,
            "seed_notes": focus_notes,
        }
    )


def normalized_request_from_planner_seed(
    seed: InvestigationPlannerSeed,
) -> NormalizedInvestigationRequest:
    if seed.outcome == "bounded_ambiguity":
        subject_context = seed.subject_context
        if subject_context.resolution_status == "ambiguous_scope":
            if subject_context.scope.ambiguous_clusters:
                raise ValueError(
                    "bounded ingress ambiguity: cluster scope candidates="
                    + ", ".join(subject_context.scope.ambiguous_clusters)
                )
            raise ValueError(
                "bounded ingress ambiguity: namespace scope candidates="
                + ", ".join(subject_context.scope.ambiguous_namespaces)
            )
        if subject_context.resolution_status == "ambiguous_subject":
            raise ValueError(
                "bounded ingress ambiguity: competing primary subjects="
                + ", ".join(_subject_ref_string(ref) for ref in subject_context.competing_subjects)
            )
        raise ValueError("no canonical investigation subject could be resolved from ingress input")

    if seed.outcome != "execution_focus_resolved":
        raise ValueError(f"planner seed outcome not yet supported for normalization: {seed.outcome}")

    execution_focus = seed.execution_focus
    if execution_focus is None:
        raise ValueError("execution focus is required when planner seed outcome is execution_focus_resolved")

    subject_context = seed.subject_context.model_copy(update={"notes": list(seed.seed_notes)})
    return NormalizedInvestigationRequest(
        source=seed.source,
        scope=execution_focus.scope,
        cluster=seed.cluster,
        namespace=seed.namespace,
        target=execution_focus.target,
        node_name=execution_focus.node_name if execution_focus.scope == "node" else None,
        service_name=execution_focus.service_name if execution_focus.scope == "service" or execution_focus.service_name else execution_focus.service_name,
        profile=execution_focus.profile,
        lookback_minutes=seed.lookback_minutes,
        normalization_notes=list(seed.seed_notes),
        subject_context=subject_context,
    )


def apply_post_seed_normalization(
    normalized: NormalizedInvestigationRequest,
    deps: PostSeedNormalizationDeps,
) -> NormalizedInvestigationRequest:
    if normalized.scope != "workload":
        return normalized

    lowered = normalized.target.strip().lower()
    if lowered not in {
        "pod",
        "pods",
        "workload",
        "workloads",
        "unhealthy",
        "unhealthy-pod",
        "unhealthy-workload",
    }:
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


def _requested_target(
    subject_set: NormalizedInvestigationSubjectSet,
    subject_context: InvestigationSubjectContext,
) -> str | None:
    if subject_set.ingress.target:
        return subject_set.ingress.target
    if subject_context.primary_subject is not None:
        return _subject_ref_string(subject_context.primary_subject)
    return None


def _lookup_cluster_token(subject_set: NormalizedInvestigationSubjectSet) -> str | None:
    if subject_set.ingress.cluster is not None:
        return subject_set.ingress.cluster
    return subject_set.scope.cluster


def _execution_focus_for_subject(
    subject_set: NormalizedInvestigationSubjectSet,
    focus: InvestigationSubjectRef,
    deps: PlannerSeedDeps,
    notes: list[str],
) -> PlannerSeedExecutionFocus:
    profile = subject_set.ingress.profile_hint
    scope = "workload"
    target = focus.name
    node_name: str | None = None
    service_name: str | None = subject_set.ingress.service_name
    lookup_cluster = _lookup_cluster_token(subject_set)

    if focus.kind == "resource_hint":
        target = deps.canonical_target(focus.name, profile, service_name)
        scope = deps.scope_from_target(target, profile)
    elif focus.kind == "pod":
        target = f"pod/{focus.name}"
    elif focus.kind == "deployment":
        target = f"deployment/{focus.name}"
    elif focus.kind == "statefulset":
        target = f"statefulset/{focus.name}"
    elif focus.kind == "service":
        target = f"service/{focus.name}"
        scope = "service"
        if profile != "service":
            notes.append("profile promoted to service based on target")
        profile = "service"
        service_name = focus.name
    elif focus.kind == "kubernetes_node":
        target = f"node/{focus.name}"
        scope = "node"
        node_name = focus.name
    elif focus.kind == "backend":
        if not subject_set.scope.namespace:
            raise ValueError("namespace is required for Backend targets")
        cluster = _resolve_cluster(deps, lookup_cluster)
        backend = deps.get_backend_cr(subject_set.scope.namespace, focus.name, cluster=cluster)
        target = f"deployment/{focus.name}"
        service_name = focus.name
        notes.append(f"resolved Backend/{focus.name} to {target}")
        if backend.get("error"):
            notes.append("backend lookup failed; using deployment fallback")
    elif focus.kind == "frontend":
        if not subject_set.scope.namespace:
            raise ValueError("namespace is required for Frontend targets")
        cluster = _resolve_cluster(deps, lookup_cluster)
        frontend = deps.get_frontend_cr(subject_set.scope.namespace, focus.name, cluster=cluster)
        if profile == "service" and "explicit_target" not in focus.sources:
            target = f"service/{focus.name}"
            scope = "service"
            profile = "service"
        else:
            target = f"deployment/{focus.name}"
        service_name = focus.name
        notes.append(f"resolved Frontend/{focus.name} to {target}")
        if frontend.get("error"):
            notes.append(f"frontend lookup failed; using {target} fallback")
    elif focus.kind == "express_cluster":
        if not subject_set.scope.namespace:
            raise ValueError("namespace is required for Cluster targets")
        cluster = _resolve_cluster(deps, lookup_cluster)
        cluster_cr = deps.get_cluster_cr(subject_set.scope.namespace, focus.name, cluster=cluster)
        if cluster_cr.get("error"):
            raise ValueError(f"cluster lookup failed for {focus.name}: {cluster_cr['error']}")
        statuses = cluster_cr.get("status", {}).get("componentStatuses") or []
        if not statuses:
            raise ValueError(f"cluster {focus.name} has no componentStatuses to investigate")
        selected = sorted(statuses, key=_cluster_component_priority)[0]
        component_kind = selected.get("kind") or ""
        component_name = selected.get("name") or ""
        if not component_kind or not component_name:
            raise ValueError(f"cluster {focus.name} has an incomplete component status entry")
        target, scope, profile, service_name = _component_target(component_kind, component_name, profile)
        notes.append(f"resolved Cluster/{focus.name} to failing component {component_kind}/{component_name}")
        notes.append(f"resolved {component_kind}/{component_name} to {target}")
    else:
        raise ValueError(f"unsupported canonical focus kind: {focus.kind}")

    if scope == "service" and profile == "workload":
        profile = "service"
        notes.append("profile promoted to service based on target")
    if subject_set.scope.cluster:
        cluster_note = f"cluster resolved from {subject_set.scope.cluster_source}: {subject_set.scope.cluster}"
        if cluster_note not in notes:
            notes.append(cluster_note)

    return PlannerSeedExecutionFocus(
        scope=scope,  # type: ignore[arg-type]
        target=target,
        profile=profile,
        node_name=node_name if scope == "node" else None,
        service_name=service_name,
    )


def _resolve_cluster(deps: PlannerSeedDeps, cluster: str | None) -> Any:
    try:
        return deps.resolve_cluster(cluster)
    except TypeError:
        return deps.resolve_cluster(cluster, {})


def _cluster_component_priority(item: dict) -> tuple[int, int, str]:
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


def _component_target(kind: str, name: str, profile: str) -> tuple[str, str, str, str | None]:
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
        return (f"statefulset/{name}", "workload", "workload", None)
    raise ValueError(f"unsupported cluster component kind for investigation: {kind}")


def _subject_ref_string(ref: InvestigationSubjectRef) -> str:
    if ref.kind == "express_cluster":
        return f"Cluster/{ref.name}"
    if ref.kind == "kubernetes_node":
        return f"node/{ref.name}"
    if ref.kind == "resource_hint":
        return ref.name
    return f"{ref.kind}/{ref.name}"
