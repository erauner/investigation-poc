import re

from .analysis import derive_findings
from .cluster_registry import resolve_cluster
from .ingress import (
    IngressDeps,
    ingress_request_from_alert_request,
    normalized_request_from_subject_set,
    normalize_ingress_request,
)
from .k8s_adapter import (
    find_unhealthy_workloads as find_unhealthy_workloads_impl,
    get_k8s_object,
    get_pod_logs,
    get_related_events,
    resolve_runtime_target,
    resolve_target,
)
from .models import (
    CollectAlertContextRequest,
    CollectContextRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    FindUnhealthyPodRequest,
    FindUnhealthyWorkloadsRequest,
    CollectedContextResponse,
    EvidenceBundle,
    NormalizedInvestigationRequest,
    ScopeType,
    UnhealthyPodResponse,
    UnhealthyWorkloadsResponse,
)
from .prom_adapter import collect_metrics_for_scope, collect_service_enrichment_metrics
from .routing import canonical_target as _canonical_target
from .routing import scope_from_target as _scope_from_target
from .settings import get_default_lookback_minutes, get_log_tail_lines


def _call_with_optional_cluster(func, *args, cluster=None, **kwargs):
    if cluster is None:
        return func(*args, **kwargs)
    try:
        return func(*args, cluster=cluster, **kwargs)
    except TypeError:
        return func(*args, **kwargs)


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


def _build_enrichment_hints(
    target_kind: str, profile: str, metrics: dict, limitations: list[str], findings: list
) -> list[str]:
    hints: list[str] = []
    if not metrics.get("prometheus_available"):
        hints.append("service metrics unavailable; use observability MCP for logs, traces, or dashboards")
    if target_kind == "node":
        hints.append("node alert; inspect recent Prometheus rules or node dashboards for pressure history")
    if any(item.title == "Pod Restarts Increasing" for item in findings):
        hints.append("high restart rate; fetch recent alert history or rollout events for this workload")
    if any("metric unavailable:" in item for item in limitations):
        hints.append("metrics were partial; enrich with observability MCP if deeper evidence is required")
    return sorted(set(hints))


def _build_operator_ownership_hints(target_kind: str, object_state: dict) -> list[str]:
    if target_kind not in {"pod", "deployment", "statefulset"} or object_state.get("error"):
        return []

    labels = object_state.get("labels") or {}
    managed_by = labels.get("app.kubernetes.io/managed-by")
    if not managed_by:
        return []

    owner_kind = labels.get("homelab.erauner.dev/owner-kind")
    owner_name = labels.get("homelab.erauner.dev/owner-name")
    if not owner_kind or not owner_name:
        ignored_kinds = {"ReplicaSet", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}
        for owner in object_state.get("ownerReferences") or []:
            candidate_kind = owner.get("kind")
            candidate_name = owner.get("name")
            if candidate_kind and candidate_name and candidate_kind not in ignored_kinds:
                owner_kind = candidate_kind
                owner_name = candidate_name
                break

    if owner_kind and owner_name:
        return [
            f"operator-managed workload ({managed_by}); owner appears to be {owner_kind}/{owner_name}. Prefer checking operator reconciliation and updating the owning resource rather than editing pods directly."
        ]

    return [
        f"operator-managed workload ({managed_by}); check operator reconciliation history before making direct workload changes."
    ]


def render_collected_context(bundle: EvidenceBundle) -> CollectedContextResponse:
    return CollectedContextResponse(
        cluster=bundle.cluster,
        target=bundle.target,
        object_state=bundle.object_state,
        events=bundle.events,
        log_excerpt=bundle.log_excerpt,
        metrics=bundle.metrics,
        findings=bundle.findings,
        limitations=bundle.limitations,
        enrichment_hints=bundle.enrichment_hints,
    )


def evidence_bundle_from_context(context: CollectedContextResponse) -> EvidenceBundle:
    return EvidenceBundle(
        cluster=getattr(context, "cluster", "current-context"),
        target=context.target,
        object_state=getattr(context, "object_state", {}),
        events=getattr(context, "events", []),
        log_excerpt=getattr(context, "log_excerpt", ""),
        metrics=getattr(context, "metrics", {}),
        findings=getattr(context, "findings", []),
        limitations=getattr(context, "limitations", []),
        enrichment_hints=getattr(context, "enrichment_hints", []),
    )


def _materialize_evidence_bundle(
    req: CollectContextRequest,
    *,
    cluster_alias: str,
    target,
    object_state: dict,
    events: list[str],
    logs: str,
    extra_limitations: list[str] | None = None,
) -> EvidenceBundle:
    cluster = resolve_cluster(req.cluster)
    effective_profile = req.profile
    effective_service_name = req.service_name
    if target.kind == "service":
        effective_profile = "service"
        effective_service_name = req.service_name or target.name
    lookback_minutes = req.lookback_minutes or get_default_lookback_minutes()
    metrics, metric_limitations = _call_with_optional_cluster(
        collect_metrics_for_scope,
        target=target,
        profile=effective_profile,
        service_name=effective_service_name,
        lookback_minutes=lookback_minutes,
        cluster=cluster,
    )
    if effective_profile == "workload" and effective_service_name and target.kind in {"pod", "deployment", "statefulset"}:
        service_metrics, _ = _call_with_optional_cluster(
            collect_service_enrichment_metrics,
            namespace=target.namespace or req.namespace or "",
            service_name=effective_service_name,
            lookback_minutes=lookback_minutes,
            cluster=cluster,
        )
        for key, value in service_metrics.items():
            if key == "prometheus_available":
                continue
            if value is not None:
                metrics[key] = value
        if service_metrics.get("prometheus_available"):
            metrics["prometheus_available"] = True
    findings = derive_findings(effective_profile, object_state, events, logs, metrics)
    limitations = [*metric_limitations, *(extra_limitations or [])]
    if object_state.get("error"):
        limitations.append("kubernetes object query failed")
    if events == ["no related events"]:
        limitations.append("no related Kubernetes events found")
    if target.kind in {"pod", "deployment", "statefulset"} and (logs.startswith("log query failed:") or logs.startswith("no pod found")):
        limitations.append("pod logs unavailable for target")
    enrichment_hints = _build_enrichment_hints(target.kind, effective_profile, metrics, limitations, findings)
    enrichment_hints.extend(_build_operator_ownership_hints(target.kind, object_state))

    return EvidenceBundle(
        cluster=cluster_alias,
        target=target,
        object_state=object_state,
        events=events,
        log_excerpt=logs[:4000],
        metrics=metrics,
        findings=findings,
        limitations=sorted(set(limitations)),
        enrichment_hints=sorted(set(enrichment_hints)),
    )


def materialize_workload_evidence(
    req: CollectContextRequest,
    *,
    target,
    object_state: dict,
    events: list[str],
    log_excerpt: str,
    cluster_alias: str | None = None,
    extra_limitations: list[str] | None = None,
) -> EvidenceBundle:
    cluster = resolve_cluster(req.cluster)
    return _materialize_evidence_bundle(
        req,
        cluster_alias=cluster_alias or cluster.alias,
        target=target,
        object_state=object_state,
        events=events,
        logs=log_excerpt,
        extra_limitations=extra_limitations,
    )


def materialize_service_evidence(
    req: CollectServiceContextRequest,
    *,
    target,
    metrics: dict,
    object_state: dict | None = None,
    events: list[str] | None = None,
    cluster_alias: str | None = None,
    extra_limitations: list[str] | None = None,
) -> EvidenceBundle:
    cluster = resolve_cluster(req.cluster)
    context_req = CollectContextRequest(
        cluster=req.cluster,
        namespace=req.namespace,
        target=_canonical_target(req.target or req.service_name, profile="service", service_name=req.service_name),
        profile="service",
        service_name=req.service_name,
        lookback_minutes=req.lookback_minutes,
    )
    effective_events = list(events or [])
    findings = derive_findings("service", object_state or {}, effective_events, "", metrics)
    limitations = [*(extra_limitations or [])]
    if (object_state or {}).get("error"):
        limitations.append("kubernetes object query failed")
    if events == ["no related events"]:
        limitations.append("no related Kubernetes events found")
    enrichment_hints = _build_enrichment_hints("service", "service", metrics, limitations, findings)
    return EvidenceBundle(
        cluster=cluster_alias or cluster.alias,
        target=target,
        object_state=object_state or {},
        events=effective_events,
        log_excerpt="",
        metrics={**metrics, "profile": "service", "lookback_minutes": context_req.lookback_minutes},
        findings=findings,
        limitations=sorted(set(limitations)),
        enrichment_hints=sorted(set(enrichment_hints)),
    )


def materialize_node_evidence(
    req: CollectNodeContextRequest,
    *,
    target,
    metrics: dict,
    object_state: dict | None = None,
    events: list[str] | None = None,
    cluster_alias: str | None = None,
    extra_limitations: list[str] | None = None,
) -> EvidenceBundle:
    cluster = resolve_cluster(req.cluster)
    findings = derive_findings("node", object_state or {}, events or ["no related events"], "", metrics)
    limitations = [*(extra_limitations or [])]
    if (object_state or {}).get("error"):
        limitations.append("kubernetes object query failed")
    if events == ["no related events"]:
        limitations.append("no related Kubernetes events found")
    enrichment_hints = _build_enrichment_hints("node", "node", metrics, limitations, findings)
    return EvidenceBundle(
        cluster=cluster_alias or cluster.alias,
        target=target,
        object_state=object_state or {},
        events=events or ["no related events"],
        log_excerpt="",
        metrics={**metrics, "profile": "node", "lookback_minutes": req.lookback_minutes},
        findings=findings,
        limitations=sorted(set(limitations)),
        enrichment_hints=sorted(set(enrichment_hints)),
    )


def collect_evidence_bundle(req: CollectContextRequest) -> EvidenceBundle:
    cluster = resolve_cluster(req.cluster)
    requested_target = _call_with_optional_cluster(resolve_target, req.namespace, req.target, cluster=cluster)
    target = _call_with_optional_cluster(resolve_runtime_target, requested_target, cluster=cluster)
    object_state = _call_with_optional_cluster(get_k8s_object, target, cluster=cluster)
    events = _call_with_optional_cluster(get_related_events, target, cluster=cluster)
    logs = ""
    if target.kind in {"pod", "deployment", "statefulset"}:
        logs = _call_with_optional_cluster(get_pod_logs, target, tail=get_log_tail_lines(), cluster=cluster)
    return _materialize_evidence_bundle(
        req,
        cluster_alias=cluster.alias,
        target=target,
        object_state=object_state,
        events=events,
        logs=logs,
    )


def _collect_context(req: CollectContextRequest) -> CollectedContextResponse:
    return render_collected_context(collect_evidence_bundle(req))


def _infer_alert_inputs(req: CollectAlertContextRequest) -> CollectContextRequest:
    normalized = normalize_alert_input(req)
    return CollectContextRequest(
        cluster=normalized.cluster,
        namespace=normalized.namespace,
        target=normalized.target,
        profile=normalized.profile,
        service_name=normalized.service_name,
        lookback_minutes=normalized.lookback_minutes,
    )


def normalize_alert_input(req: CollectAlertContextRequest) -> NormalizedInvestigationRequest:
    ingress_req = ingress_request_from_alert_request(req)
    deps = IngressDeps(
        canonical_target=_canonical_target,
        scope_from_target=_scope_from_target,
        resolve_cluster=resolve_cluster,
        get_backend_cr=lambda *args, **kwargs: {},
        get_frontend_cr=lambda *args, **kwargs: {},
        get_cluster_cr=lambda *args, **kwargs: {},
        find_unhealthy_pod=find_unhealthy_pod,
    )
    subject_set = normalize_ingress_request(ingress_req, deps)
    try:
        normalized = normalized_request_from_subject_set(subject_set, deps)
    except ValueError as exc:
        if "no canonical investigation subject could be resolved from ingress input" in str(exc):
            raise ValueError("target could not be inferred from alert input") from exc
        raise
    if not normalized.lookback_minutes:
        normalized = normalized.model_copy(update={"lookback_minutes": get_default_lookback_minutes()})
    return normalized


def collect_workload_evidence(req: CollectContextRequest) -> EvidenceBundle:
    return collect_evidence_bundle(req)


def find_unhealthy_workloads(req: FindUnhealthyWorkloadsRequest) -> UnhealthyWorkloadsResponse:
    cluster = resolve_cluster(req.cluster)
    return find_unhealthy_workloads_impl(namespace=req.namespace, limit=req.limit, cluster=cluster)


def find_unhealthy_pod(req: FindUnhealthyPodRequest) -> UnhealthyPodResponse:
    cluster = resolve_cluster(req.cluster)
    workloads = find_unhealthy_workloads_impl(namespace=req.namespace, limit=1, cluster=cluster)
    candidate = workloads.candidates[0] if workloads.candidates else None
    limitations = list(workloads.limitations)
    if candidate is None:
        limitations.append("no unhealthy pod found in namespace")
    return UnhealthyPodResponse(
        cluster=cluster.alias,
        namespace=req.namespace,
        candidate=candidate,
        limitations=sorted(set(limitations)),
    )


def collect_node_evidence(req: CollectNodeContextRequest) -> EvidenceBundle:
    return collect_evidence_bundle(
        CollectContextRequest(
            cluster=req.cluster,
            namespace=None,
            target=f"node/{req.node_name}",
            profile="workload",
            lookback_minutes=req.lookback_minutes,
        )
    )


def collect_service_evidence(req: CollectServiceContextRequest) -> EvidenceBundle:
    target = _canonical_target(req.target or req.service_name, profile="service", service_name=req.service_name)
    return collect_evidence_bundle(
        CollectContextRequest(
            cluster=req.cluster,
            namespace=req.namespace,
            target=target,
            profile="service",
            service_name=req.service_name,
            lookback_minutes=req.lookback_minutes,
        )
    )


def collect_alert_evidence(req: CollectAlertContextRequest) -> EvidenceBundle:
    return collect_evidence_bundle(_infer_alert_inputs(req))
