from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .models import (
    BuildInvestigationPlanRequest,
    CollectAlertContextRequest,
    ConfidenceType,
    InvestigationIngressRequest,
    InvestigationReportRequest,
    InvestigationSubjectContext,
    InvestigationSubjectRef,
    NormalizedInvestigationSubjectSet,
    ResolvedIngressScope,
)

_RESOURCE_REF_PATTERN = re.compile(
    r"(?P<kind>pod|deployment|statefulset|service|backend|frontend|cluster|node)/(?P<name>[a-z0-9][a-z0-9\-\.]*)",
    re.IGNORECASE,
)
_UNSUPPORTED_RESOURCE_REF_PATTERN = re.compile(
    r"(?P<kind>daemonset|job)/(?P<name>[a-z0-9][a-z0-9\-\.]*)",
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
    resolve_cluster: Callable[..., Any]
    get_cluster_cr: Callable[..., dict]


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
    candidates = _extract_candidate_refs(req, scope, notes)
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
def subject_context_from_subject_set(
    subject_set: NormalizedInvestigationSubjectSet,
) -> InvestigationSubjectContext:
    if subject_set.scope.ambiguous_clusters or subject_set.scope.ambiguous_namespaces:
        status = "ambiguous_scope"
    elif subject_set.canonical_focus is not None:
        status = "resolved"
    else:
        competing = _competing_subjects(subject_set)
        status = "ambiguous_subject" if competing else "unresolved"
    return InvestigationSubjectContext(
        resolution_status=status,
        scope=subject_set.scope.model_copy(deep=True),
        primary_subject=subject_set.canonical_focus.model_copy(deep=True) if subject_set.canonical_focus else None,
        related_subjects=[ref.model_copy(deep=True) for ref in subject_set.related_refs],
        competing_subjects=[ref.model_copy(deep=True) for ref in _competing_subjects(subject_set)],
        notes=list(subject_set.normalization_notes),
    )


def _competing_subjects(
    subject_set: NormalizedInvestigationSubjectSet,
) -> list[InvestigationSubjectRef]:
    if subject_set.scope.ambiguous_clusters or subject_set.scope.ambiguous_namespaces:
        return []
    if subject_set.canonical_focus is not None:
        return []
    return [ref for ref in subject_set.candidate_refs if ref.kind != "alert"]


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
    elif _match_group(_NAMESPACE_PATTERN, req.raw_text or req.question or "", "namespace"):
        scope.namespace = _match_group(_NAMESPACE_PATTERN, req.raw_text or req.question or "", "namespace")
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
        resolved_cluster = _resolve_cluster(deps, cluster_value, req.labels)
        scope.cluster = _resolved_cluster_value(resolved_cluster)
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
    notes: list[str],
) -> list[InvestigationSubjectRef]:
    _raise_for_unsupported_resource_like_input(req)
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

    if not _has_operational_ref(refs):
        vague_hint = _vague_workload_hint(req)
        if vague_hint is not None:
            refs.append(
                InvestigationSubjectRef(
                    kind="resource_hint",
                    name=vague_hint,
                    cluster=scope.cluster,
                    namespace=scope.namespace,
                    confidence="medium",
                    sources=["vague_workload"],
                )
            )

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
    ranked = sorted(
        operational,
        key=lambda ref: (
            min(_SOURCE_PRIORITY.get(source, 99) for source in ref.sources) if ref.sources else 99,
            -_CONFIDENCE_PRIORITY.get(ref.confidence, 0),
            _RELATION_PRIORITY.get(ref.relation, 99),
            _KIND_PRIORITY.get(ref.kind, 99),
            ref.namespace or "",
            ref.name,
        ),
    )
    if len(ranked) > 1:
        top = ranked[0]
        second = ranked[1]
        top_source = min(_SOURCE_PRIORITY.get(source, 99) for source in top.sources) if top.sources else 99
        second_source = min(_SOURCE_PRIORITY.get(source, 99) for source in second.sources) if second.sources else 99
        top_kind = _KIND_PRIORITY.get(top.kind, 99)
        second_kind = _KIND_PRIORITY.get(second.kind, 99)
        if (
            top_source == second_source
            and _CONFIDENCE_PRIORITY.get(top.confidence, 0) == _CONFIDENCE_PRIORITY.get(second.confidence, 0)
            and top_kind == second_kind
            and (top.kind, top.name) != (second.kind, second.name)
        ):
            return None
        if (
            top_source == second_source
            and "question_text" in top.sources
            and "question_text" in second.sources
            and top.relation == second.relation == "candidate"
            and (top.kind, top.name) != (second.kind, second.name)
        ):
            return None
    return ranked[0]
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


def _raise_for_unsupported_resource_like_input(req: InvestigationIngressRequest) -> None:
    values = [req.target, *req.explicit_refs, req.raw_text, req.question]
    for value in values:
        if not value:
            continue
        match = _UNSUPPORTED_RESOURCE_REF_PATTERN.search(value)
        if match is not None:
            raise ValueError(f"unsupported investigation subject kind: {match.group('kind').lower()}")


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


def _vague_workload_hint(req: InvestigationIngressRequest) -> str | None:
    text = " ".join(part for part in [req.target, req.question, req.raw_text] if part).lower()
    if "unhealthy pod" in text:
        return "pod"
    if "unhealthy workload" in text or "unhealthy workloads" in text:
        return "workload"
    return None


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
