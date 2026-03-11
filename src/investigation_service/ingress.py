from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .models import (
    BuildInvestigationPlanRequest,
    CollectAlertContextRequest,
    ConfidenceType,
    FindUnhealthyPodRequest,
    InvestigationIngressRequest,
    InvestigationReportRequest,
    InvestigationSubjectRef,
    NormalizedInvestigationRequest,
    NormalizedInvestigationSubjectSet,
    ProfileType,
    ResolvedIngressScope,
)

_RESOURCE_REF_PATTERN = re.compile(
    r"(?P<kind>pod|deployment|statefulset|service|backend|frontend|cluster|node)/(?P<name>[a-z0-9][a-z0-9\-\.]*)",
    re.IGNORECASE,
)
_NAMESPACE_PATTERN = re.compile(r"\bnamespace\s+(?P<namespace>[a-z0-9][a-z0-9-]*)\b", re.IGNORECASE)
_IN_CLUSTER_PATTERN = re.compile(r"\bin\s+cluster\s+(?P<cluster>[a-z0-9][a-z0-9-]*)\b", re.IGNORECASE)
_FIELD_PATTERN = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9 _-]*):\s*(?P<value>.+)$")
_VAGUE_WORKLOAD_PATTERNS = (
    "unhealthy pod",
    "unhealthy workload",
    "unhealthy workloads",
)

_SOURCE_PRIORITY = {
    "explicit_target": 0,
    "explicit_ref": 1,
    "service_hint": 2,
    "node_hint": 3,
    "question_text": 4,
    "alert_field": 5,
    "alert_label": 6,
    "alert_annotation": 7,
    "vague_workload": 8,
    "express_enrichment": 9,
}
_KIND_PRIORITY = {
    "pod": 0,
    "backend": 1,
    "frontend": 2,
    "express_cluster": 3,
    "statefulset": 4,
    "deployment": 5,
    "service": 6,
    "kubernetes_node": 7,
    "resource_hint": 8,
    "alert": 9,
}
_CONFIDENCE_PRIORITY: dict[ConfidenceType, int] = {"high": 2, "medium": 1, "low": 0}
_RELATION_PRIORITY = {"candidate": 0, "related": 1, "member": 2, "dependency": 3}


@dataclass(frozen=True)
class IngressDeps:
    canonical_target: Callable[[str, str, str | None], str]
    scope_from_target: Callable[[str, str], str]
    resolve_cluster: Callable[..., Any]
    get_backend_cr: Callable[..., dict]
    get_frontend_cr: Callable[..., dict]
    get_cluster_cr: Callable[..., dict]
    find_unhealthy_pod: Callable[[FindUnhealthyPodRequest], Any]


def ingress_request_from_report_request(req: InvestigationReportRequest) -> InvestigationIngressRequest:
    return InvestigationIngressRequest(
        source="alert" if req.alertname else "manual",
        question=req.question,
        raw_text=req.question,
        cluster=req.cluster,
        namespace=req.namespace,
        target=req.target,
        profile_hint=req.profile,
        service_name=req.service_name,
        node_name=req.node_name,
        lookback_minutes=req.lookback_minutes,
        alertname=req.alertname,
        labels=req.labels,
        annotations=req.annotations,
    )


def ingress_request_from_plan_request(req: BuildInvestigationPlanRequest) -> InvestigationIngressRequest:
    return InvestigationIngressRequest(
        source="alert" if req.alertname else "manual",
        question=req.question,
        raw_text=req.question,
        cluster=req.cluster,
        namespace=req.namespace,
        target=req.target,
        profile_hint=req.profile,
        service_name=req.service_name,
        node_name=req.node_name,
        lookback_minutes=req.lookback_minutes,
        alertname=req.alertname,
        labels=req.labels,
        annotations=req.annotations,
    )


def ingress_request_from_alert_request(req: CollectAlertContextRequest) -> InvestigationIngressRequest:
    return InvestigationIngressRequest(
        source="alert",
        cluster=req.cluster,
        namespace=req.namespace,
        target=req.target,
        profile_hint=req.profile,
        service_name=req.service_name,
        node_name=req.node_name,
        lookback_minutes=req.lookback_minutes,
        alertname=req.alertname,
        labels=req.labels,
        annotations=req.annotations,
    )


def normalize_ingress_request(
    req: InvestigationIngressRequest,
    deps: IngressDeps,
) -> NormalizedInvestigationSubjectSet:
    notes: list[str] = []
    scope = _resolve_scope(req, deps, notes)
    candidates = _extract_candidate_refs(req, scope, deps, notes)
    candidates = _dedupe_refs(candidates)
    related_refs = _build_related_refs(candidates, scope, deps, notes)
    canonical_focus = _select_canonical_focus(req, scope, candidates, related_refs)
    return NormalizedInvestigationSubjectSet(
        ingress=req,
        scope=scope,
        candidate_refs=candidates,
        canonical_focus=canonical_focus,
        related_refs=related_refs,
        normalization_notes=notes,
    )


def normalized_request_from_subject_set(
    subject_set: NormalizedInvestigationSubjectSet,
    deps: IngressDeps,
) -> NormalizedInvestigationRequest:
    if subject_set.scope.ambiguous_clusters:
        raise ValueError(
            "cluster scope is ambiguous: " + ", ".join(subject_set.scope.ambiguous_clusters)
        )
    if subject_set.scope.ambiguous_namespaces:
        raise ValueError(
            "namespace scope is ambiguous: " + ", ".join(subject_set.scope.ambiguous_namespaces)
        )
    focus = subject_set.canonical_focus
    if focus is None:
        raise ValueError("no canonical investigation subject could be resolved from ingress input")

    normalized = _normalized_request_for_focus(subject_set, focus, deps)
    notes = list(normalized.normalization_notes)
    notes.append(f"canonical focus selected: {_subject_ref_string(focus)}")
    if subject_set.related_refs:
        notes.append(
            "related refs preserved: "
            + ", ".join(
                f"{_subject_ref_string(ref)} ({ref.relation})" for ref in subject_set.related_refs
            )
        )
    return normalized.model_copy(update={"normalization_notes": notes})


def canonical_focus_ref(
    subject_set: NormalizedInvestigationSubjectSet,
    fallback_target: str | None = None,
) -> str | None:
    if fallback_target:
        return fallback_target
    focus = subject_set.canonical_focus
    if focus is None:
        return None
    return _subject_ref_string(focus)


def _resolve_scope(
    req: InvestigationIngressRequest,
    deps: IngressDeps,
    notes: list[str],
) -> ResolvedIngressScope:
    field_values = _field_map(req.raw_text or req.question or "")

    namespace_candidates = _ordered_unique(
        [
            req.namespace,
            field_values.get("namespace"),
            _match_group(_NAMESPACE_PATTERN, req.raw_text or req.question or "", "namespace"),
            _label_value(req.labels, "namespace", "kubernetes_namespace", "exported_namespace"),
        ]
    )
    cluster_candidates = _ordered_unique(
        [
            req.cluster,
            field_values.get("cluster"),
            _match_group(_IN_CLUSTER_PATTERN, req.raw_text or req.question or "", "cluster"),
        ]
    )

    scope = ResolvedIngressScope()
    if req.namespace:
        scope.namespace = req.namespace
        scope.namespace_source = "explicit"
    elif field_values.get("namespace"):
        scope.namespace = field_values["namespace"]
        scope.namespace_source = "question_text"
    elif _label_value(req.labels, "namespace", "kubernetes_namespace", "exported_namespace"):
        scope.namespace = _label_value(req.labels, "namespace", "kubernetes_namespace", "exported_namespace")
        scope.namespace_source = "alert_label"

    if len(namespace_candidates) > 1 and req.namespace is None:
        scope.ambiguous_namespaces = namespace_candidates
        notes.append("namespace ambiguity detected: " + ", ".join(namespace_candidates))

    if req.cluster:
        resolved_cluster = _resolve_cluster(deps, req.cluster, req.labels)
        scope.cluster = _resolved_cluster_value(resolved_cluster)
        scope.cluster_source = "explicit"
        notes.append(f"cluster resolved from explicit: {scope.cluster}")
    elif field_values.get("cluster") or _match_group(_IN_CLUSTER_PATTERN, req.raw_text or req.question or "", "cluster"):
        cluster_value = field_values.get("cluster") or _match_group(_IN_CLUSTER_PATTERN, req.raw_text or req.question or "", "cluster")
        scope.cluster = cluster_value
        scope.cluster_source = "question_text"
        notes.append(f"cluster resolved from text: {scope.cluster}")
    elif req.alertname:
        resolved_cluster = _resolve_cluster(deps, None, req.labels)
        scope.cluster = _resolved_cluster_value(resolved_cluster)
        scope.cluster_source = "alert_label" if getattr(resolved_cluster, "source", None) == "alert_label" else "default"
        notes.append(f"cluster resolved from {getattr(resolved_cluster, 'source', 'unknown')}: {scope.cluster}")

    if len(cluster_candidates) > 1 and req.cluster is None:
        scope.ambiguous_clusters = cluster_candidates
        notes.append("cluster ambiguity detected: " + ", ".join(cluster_candidates))

    return scope


def _extract_candidate_refs(
    req: InvestigationIngressRequest,
    scope: ResolvedIngressScope,
    deps: IngressDeps,
    notes: list[str],
) -> list[InvestigationSubjectRef]:
    refs: list[InvestigationSubjectRef] = []
    if req.target:
        refs.append(_subject_ref_from_string(req.target, scope, source="explicit_target", confidence="high"))
    for ref in req.explicit_refs:
        refs.append(_subject_ref_from_string(ref, scope, source="explicit_ref", confidence="high"))
    if req.service_name and not req.target:
        refs.append(
            InvestigationSubjectRef(
                kind="service",
                name=req.service_name,
                cluster=scope.cluster,
                namespace=scope.namespace,
                confidence="medium",
                sources=["service_hint"],
            )
        )
    if req.node_name and not req.target:
        refs.append(
            InvestigationSubjectRef(
                kind="kubernetes_node",
                name=req.node_name,
                cluster=scope.cluster,
                namespace=None,
                confidence="medium",
                sources=["node_hint"],
            )
        )
    if req.alertname:
        refs.append(
            InvestigationSubjectRef(
                kind="alert",
                name=req.alertname,
                cluster=scope.cluster,
                namespace=scope.namespace,
                confidence="medium",
                sources=["alert_field"],
            )
        )

    text = req.raw_text or req.question or ""
    for kind, name in _resource_refs_from_text(text):
        refs.append(
            InvestigationSubjectRef(
                kind=_normalize_subject_kind(kind),
                name=name,
                cluster=scope.cluster,
                namespace=None if kind.lower() == "node" else scope.namespace,
                confidence="medium",
                sources=["question_text"],
            )
        )

    if req.alertname and not _has_operational_ref(refs):
        alert_target = _infer_alert_target(req, scope)
        if alert_target is not None:
            refs.append(alert_target)
            if "alert_label" in alert_target.sources and alert_target.kind == "statefulset":
                notes.append("inferred target from statefulset labels")
            elif "alert_annotation" in alert_target.sources:
                notes.append("inferred target from alert text")

    if not _has_operational_ref(refs) and _should_expand_vague_workload(req):
        if not scope.namespace:
            raise ValueError("namespace is required when resolving a vague workload target")
        unhealthy = deps.find_unhealthy_pod(
            FindUnhealthyPodRequest(cluster=scope.cluster, namespace=scope.namespace)
        )
        candidate = getattr(unhealthy, "candidate", None)
        if candidate is None:
            notes.append("no unhealthy pod found for vague workload request")
        else:
            refs.append(
                InvestigationSubjectRef(
                    kind="pod",
                    name=candidate.target.split("/", 1)[1],
                    cluster=scope.cluster,
                    namespace=scope.namespace,
                    confidence="medium",
                    sources=["vague_workload"],
                )
            )
            notes.append(f"resolved vague workload target to {candidate.target}")

    return refs


def _build_related_refs(
    candidates: list[InvestigationSubjectRef],
    scope: ResolvedIngressScope,
    deps: IngressDeps,
    notes: list[str],
) -> list[InvestigationSubjectRef]:
    related: list[InvestigationSubjectRef] = []
    for ref in candidates:
        if ref.kind != "express_cluster" or not scope.namespace:
            continue
        cluster = _resolve_cluster(deps, scope.cluster, {})
        cluster_cr = deps.get_cluster_cr(scope.namespace, ref.name, cluster=cluster)
        if cluster_cr.get("error"):
            notes.append(f"cluster lookup failed for {ref.name}: {cluster_cr['error']}")
            continue
        statuses = cluster_cr.get("status", {}).get("componentStatuses") or []
        for item in statuses:
            kind = _normalize_subject_kind(item.get("kind", ""))
            name = item.get("name") or ""
            if not name or kind not in {"backend", "frontend", "statefulset", "deployment", "service"}:
                continue
            relation = "member" if kind in {"backend", "frontend"} else "dependency"
            related.append(
                InvestigationSubjectRef(
                    kind=kind,
                    name=name,
                    cluster=scope.cluster,
                    namespace=scope.namespace,
                    confidence="medium",
                    sources=["express_enrichment"],
                    relation=relation,
                )
            )
    return _dedupe_refs(related)


def _select_canonical_focus(
    req: InvestigationIngressRequest,
    scope: ResolvedIngressScope,
    candidates: list[InvestigationSubjectRef],
    related_refs: list[InvestigationSubjectRef],
) -> InvestigationSubjectRef | None:
    if scope.ambiguous_clusters or scope.ambiguous_namespaces:
        return None

    operational = [ref for ref in candidates if ref.kind != "alert"]
    if not operational:
        return None

    explicit_target_refs = [ref for ref in operational if "explicit_target" in ref.sources]
    if explicit_target_refs:
        return explicit_target_refs[0]

    express_clusters = [ref for ref in operational if ref.kind == "express_cluster"]
    if len(express_clusters) == 1:
        express_ref = express_clusters[0]
        same_scope = [
            ref for ref in operational if ref is not express_ref and ref.cluster == express_ref.cluster and ref.namespace == express_ref.namespace
        ]
        allowed = {
            (ref.kind, ref.name)
            for ref in related_refs
            if ref.cluster == express_ref.cluster and ref.namespace == express_ref.namespace
        }
        if all((ref.kind, ref.name) in allowed for ref in same_scope):
            return express_ref

    return sorted(
        operational,
        key=lambda ref: (
            min(_SOURCE_PRIORITY.get(source, 99) for source in ref.sources) if ref.sources else 99,
            _KIND_PRIORITY.get(ref.kind, 99),
            ref.namespace or "",
            ref.name,
        ),
    )[0]


def _normalized_request_for_focus(
    subject_set: NormalizedInvestigationSubjectSet,
    focus: InvestigationSubjectRef,
    deps: IngressDeps,
) -> NormalizedInvestigationRequest:
    source = "alert" if subject_set.ingress.alertname else "manual"
    notes = list(subject_set.normalization_notes)
    profile = subject_set.ingress.profile_hint
    scope = "workload"
    target = focus.name
    node_name: str | None = None
    service_name: str | None = subject_set.ingress.service_name

    if focus.kind == "resource_hint":
        target = deps.canonical_target(focus.name, profile, service_name)
        scope = deps.scope_from_target(target, profile)
    elif focus.kind == "pod":
        target = f"pod/{focus.name}"
        scope = "workload"
    elif focus.kind == "deployment":
        target = f"deployment/{focus.name}"
        scope = "workload"
    elif focus.kind == "statefulset":
        target = f"statefulset/{focus.name}"
        scope = "workload"
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
        cluster = _resolve_cluster(deps, subject_set.scope.cluster, {})
        backend = deps.get_backend_cr(subject_set.scope.namespace, focus.name, cluster=cluster)
        target = f"deployment/{focus.name}"
        scope = "workload"
        service_name = focus.name
        notes.append(f"resolved Backend/{focus.name} to {target}")
        if backend.get("error"):
            notes.append("backend lookup failed; using deployment fallback")
    elif focus.kind == "frontend":
        cluster = _resolve_cluster(deps, subject_set.scope.cluster, {})
        frontend = deps.get_frontend_cr(subject_set.scope.namespace, focus.name, cluster=cluster)
        if profile == "service" and "explicit_target" not in focus.sources:
            target = f"service/{focus.name}"
            scope = "service"
            profile = "service"
        else:
            target = f"deployment/{focus.name}"
            scope = "workload"
        service_name = focus.name
        notes.append(f"resolved Frontend/{focus.name} to {target}")
        if frontend.get("error"):
            notes.append(f"frontend lookup failed; using {target} fallback")
    elif focus.kind == "express_cluster":
        if not subject_set.scope.namespace:
            raise ValueError("namespace is required for Cluster targets")
        cluster = _resolve_cluster(deps, subject_set.scope.cluster, {})
        cluster_cr = deps.get_cluster_cr(subject_set.scope.namespace, focus.name, cluster=cluster)
        if cluster_cr.get("error"):
            target = f"Cluster/{focus.name}"
            scope = "workload"
            notes.append(f"cluster lookup failed for {focus.name}; retaining logical cluster target")
            return NormalizedInvestigationRequest(
                source=source,
                scope=scope,
                cluster=subject_set.scope.cluster,
                namespace=subject_set.scope.namespace,
                target=target,
                node_name=None,
                service_name=service_name,
                profile=profile,
                lookback_minutes=subject_set.ingress.lookback_minutes,
                normalization_notes=notes,
            )
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

    return NormalizedInvestigationRequest(
        source=source,
        scope=scope,  # type: ignore[arg-type]
        cluster=subject_set.scope.cluster,
        namespace=subject_set.scope.namespace,
        target=target,
        node_name=node_name if scope == "node" else None,
        service_name=service_name if scope == "service" or service_name else service_name,
        profile=profile,
        lookback_minutes=subject_set.ingress.lookback_minutes,
        normalization_notes=notes,
    )


def _infer_alert_target(
    req: InvestigationIngressRequest,
    scope: ResolvedIngressScope,
) -> InvestigationSubjectRef | None:
    if req.target:
        return None
    if req.node_name:
        return InvestigationSubjectRef(
            kind="kubernetes_node",
            name=req.node_name,
            cluster=scope.cluster,
            confidence="high",
            sources=["alert_field"],
        )
    if req.service_name:
        return InvestigationSubjectRef(
            kind="service",
            name=req.service_name,
            cluster=scope.cluster,
            namespace=scope.namespace,
            confidence="high",
            sources=["alert_field"],
        )

    text = _first_non_empty(
        _annotation_value(req.annotations, "summary", "description", "message"),
        _label_value(req.labels, "summary"),
    )
    inferred = _infer_target_from_text(text)
    if inferred:
        return _subject_ref_from_string(inferred, scope, source="alert_annotation", confidence="medium")

    pod_name = _label_value(req.labels, "pod", "pod_name", "kubernetes_pod_name")
    deployment_name = _label_value(req.labels, "deployment", "deployment_name", "kubernetes_deployment_name")
    statefulset_name = _label_value(req.labels, "statefulset", "statefulset_name", "kubernetes_statefulset_name")
    service_name = _label_value(req.labels, "service", "service_name")
    node_name = _label_value(req.labels, "node", "node_name", "kubernetes_node", "instance")
    if pod_name:
        return InvestigationSubjectRef(kind="pod", name=pod_name, cluster=scope.cluster, namespace=scope.namespace, confidence="high", sources=["alert_label"])
    if deployment_name:
        return InvestigationSubjectRef(kind="deployment", name=deployment_name, cluster=scope.cluster, namespace=scope.namespace, confidence="high", sources=["alert_label"])
    if statefulset_name:
        return InvestigationSubjectRef(kind="statefulset", name=statefulset_name, cluster=scope.cluster, namespace=scope.namespace, confidence="high", sources=["alert_label"])
    if service_name:
        return InvestigationSubjectRef(kind="service", name=service_name, cluster=scope.cluster, namespace=scope.namespace, confidence="high", sources=["alert_label"])
    if node_name:
        return InvestigationSubjectRef(kind="kubernetes_node", name=node_name, cluster=scope.cluster, confidence="high", sources=["alert_label"])
    return None


def _resolve_cluster(deps: IngressDeps, cluster: str | None, labels: dict[str, str] | None) -> Any:
    try:
        return deps.resolve_cluster(cluster, labels)
    except TypeError:
        return deps.resolve_cluster(cluster)


def _resolved_cluster_value(cluster: Any) -> str | None:
    if getattr(cluster, "source", None) == "legacy_current_context":
        return None
    return getattr(cluster, "alias", None)


def _subject_ref_from_string(
    value: str,
    scope: ResolvedIngressScope,
    *,
    source: str,
    confidence: ConfidenceType,
) -> InvestigationSubjectRef:
    if "/" not in value:
        return InvestigationSubjectRef(
            kind="resource_hint",
            name=value,
            cluster=scope.cluster,
            namespace=scope.namespace,
            confidence=confidence,
            sources=[source],  # type: ignore[list-item]
        )
    kind, name = value.split("/", 1)
    normalized_kind = _normalize_subject_kind(kind)
    return InvestigationSubjectRef(
        kind=normalized_kind,
        name=name,
        cluster=scope.cluster,
        namespace=None if normalized_kind == "kubernetes_node" else scope.namespace,
        confidence=confidence,
        sources=[source],  # type: ignore[list-item]
    )


def _normalize_subject_kind(kind: str) -> str:
    lowered = kind.strip().lower()
    mapping = {
        "cluster": "express_cluster",
        "node": "kubernetes_node",
    }
    return mapping.get(lowered, lowered)


def _subject_ref_string(ref: InvestigationSubjectRef) -> str:
    if ref.kind == "express_cluster":
        return f"Cluster/{ref.name}"
    if ref.kind == "kubernetes_node":
        return f"node/{ref.name}"
    if ref.kind == "resource_hint":
        return ref.name
    return f"{ref.kind}/{ref.name}"


def _resource_refs_from_text(text: str) -> list[tuple[str, str]]:
    return [(match.group("kind"), match.group("name")) for match in _RESOURCE_REF_PATTERN.finditer(text or "")]


def _field_map(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        match = _FIELD_PATTERN.match(line.strip())
        if match is None:
            continue
        fields[match.group("key").strip().lower()] = match.group("value").strip()
    return fields


def _match_group(pattern: re.Pattern[str], text: str, group: str) -> str | None:
    match = pattern.search(text or "")
    if match is None:
        return None
    return match.group(group)


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _label_value(labels: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = labels.get(key)
        if value:
            return value
    return None


def _annotation_value(annotations: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = annotations.get(key)
        if value:
            return value
    return None


def _ordered_unique(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _has_operational_ref(refs: list[InvestigationSubjectRef]) -> bool:
    return any(ref.kind != "alert" for ref in refs)


def _should_expand_vague_workload(req: InvestigationIngressRequest) -> bool:
    text = " ".join(part for part in [req.target, req.question, req.raw_text] if part).lower()
    return any(pattern in text for pattern in _VAGUE_WORKLOAD_PATTERNS)


def _dedupe_refs(refs: list[InvestigationSubjectRef]) -> list[InvestigationSubjectRef]:
    merged: dict[tuple[str, str, str | None, str | None], InvestigationSubjectRef] = {}
    order: list[tuple[str, str, str | None, str | None]] = []
    for ref in refs:
        key = (ref.kind, ref.name, ref.cluster, ref.namespace)
        if key not in merged:
            merged[key] = ref
            order.append(key)
            continue
        existing = merged[key]
        confidence = (
            ref.confidence
            if _CONFIDENCE_PRIORITY[ref.confidence] > _CONFIDENCE_PRIORITY[existing.confidence]
            else existing.confidence
        )
        relation = (
            ref.relation
            if _RELATION_PRIORITY[ref.relation] > _RELATION_PRIORITY[existing.relation]
            else existing.relation
        )
        sources = _ordered_unique([*existing.sources, *ref.sources])
        merged[key] = existing.model_copy(update={"confidence": confidence, "relation": relation, "sources": sources})
    return [merged[key] for key in order]


def _infer_target_from_text(text: str | None) -> str | None:
    if not text:
        return None
    lower = text.lower()
    patterns = [
        (r"\bpod\s+([a-z0-9][a-z0-9\-\.]*)\b", "pod"),
        (r"\bdeployment\s+([a-z0-9][a-z0-9\-\.]*)\b", "deployment"),
        (r"\bstatefulset\s+([a-z0-9][a-z0-9\-\.]*)\b", "statefulset"),
        (r"\bservice\s+([a-z0-9][a-z0-9\-\.]*)\b", "service"),
        (r"\bnode\s+([a-z0-9][a-z0-9\-\.]*)\b", "node"),
    ]
    for pattern, kind in patterns:
        match = re.search(pattern, lower)
        if match:
            return f"{kind}/{match.group(1)}"
    return None


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


def _component_target(kind: str, name: str, profile: ProfileType) -> tuple[str, str, ProfileType, str | None]:
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
