import re

from .analysis import derive_findings
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
    NormalizedInvestigationRequest,
    RootCauseReport,
    ScopeType,
    UnhealthyPodResponse,
    UnhealthyWorkloadsResponse,
)
from .prom_adapter import collect_metrics_for_scope
from .settings import get_default_lookback_minutes, get_log_tail_lines


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
        (r"\bservice\s+([a-z0-9][a-z0-9\-\.]*)\b", "service"),
        (r"\bnode\s+([a-z0-9][a-z0-9\-\.]*)\b", "node"),
    ]

    for pattern, kind in patterns:
        match = re.search(pattern, lower)
        if match:
            return f"{kind}/{match.group(1)}"
    return None


def _scope_from_target(target: str, profile: str) -> ScopeType:
    if target.startswith("node/"):
        return "node"
    if target.startswith("service/") or profile == "service":
        return "service"
    if profile == "otel-pipeline":
        return "otel-pipeline"
    return "workload"


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


def _collect_context(req: CollectContextRequest) -> CollectedContextResponse:
    requested_target = resolve_target(req.namespace, req.target)
    target = resolve_runtime_target(requested_target)
    object_state = get_k8s_object(target)
    events = get_related_events(target)
    logs = get_pod_logs(target, tail=get_log_tail_lines())
    lookback_minutes = req.lookback_minutes or get_default_lookback_minutes()
    metrics, metric_limitations = collect_metrics_for_scope(
        target=target,
        profile=req.profile,
        service_name=req.service_name,
        lookback_minutes=lookback_minutes,
    )
    findings = derive_findings(req.profile, object_state, events, logs, metrics)
    limitations = list(metric_limitations)
    if object_state.get("error"):
        limitations.append("kubernetes object query failed")
    if events == ["no related events"]:
        limitations.append("no related Kubernetes events found")
    if logs.startswith("log query failed:") or logs.startswith("no pod found"):
        limitations.append("pod logs unavailable for target")
    enrichment_hints = _build_enrichment_hints(target.kind, req.profile, metrics, limitations, findings)

    return CollectedContextResponse(
        target=target,
        object_state=object_state,
        events=events,
        log_excerpt=logs[:4000],
        metrics=metrics,
        findings=findings,
        limitations=sorted(set(limitations)),
        enrichment_hints=enrichment_hints,
    )


def _infer_alert_inputs(req: CollectAlertContextRequest) -> CollectContextRequest:
    normalized = normalize_alert_input(req)
    return CollectContextRequest(
        namespace=normalized.namespace,
        target=normalized.target,
        profile=normalized.profile,
        service_name=normalized.service_name,
        lookback_minutes=normalized.lookback_minutes,
    )


def normalize_alert_input(req: CollectAlertContextRequest) -> NormalizedInvestigationRequest:
    labels = req.labels
    annotations = req.annotations
    notes: list[str] = [f"alertname={req.alertname}"]

    target = req.target or (f"node/{req.node_name}" if req.node_name else None)
    if target:
        notes.append("target derived from explicit input")
    if not target and req.service_name:
        target = f"service/{req.service_name}"
        notes.append("target derived from explicit service_name")
    if not target:
        text_target = _infer_target_from_text(
            _first_non_empty(
                _annotation_value(annotations, "summary", "description", "message"),
                labels.get("summary"),
            )
        )
        if text_target:
            target = text_target
            notes.append("target inferred from alert text")

    namespace = _first_non_empty(
        req.namespace,
        _label_value(labels, "namespace", "kubernetes_namespace", "exported_namespace"),
    )
    if namespace:
        notes.append("namespace derived from explicit input or labels")

    if not target:
        pod_name = _label_value(labels, "pod", "pod_name", "kubernetes_pod_name")
        deployment_name = _label_value(labels, "deployment", "deployment_name", "kubernetes_deployment_name")
        statefulset_name = _label_value(labels, "statefulset", "statefulset_name", "kubernetes_statefulset_name")
        daemonset_name = _label_value(labels, "daemonset", "daemonset_name", "kubernetes_daemonset_name")
        service_name = _label_value(labels, "service", "service_name")
        node_name = req.node_name or _label_value(labels, "node", "node_name", "kubernetes_node", "instance")
        app_name = _label_value(labels, "app", "app_kubernetes_io_name", "job")
        if pod_name:
            target = f"pod/{pod_name}"
            notes.append("target inferred from pod labels")
        elif deployment_name:
            target = f"deployment/{deployment_name}"
            notes.append("target inferred from deployment labels")
        elif statefulset_name:
            target = f"deployment/{statefulset_name}"
            notes.append("target inferred from statefulset labels")
        elif daemonset_name:
            target = f"deployment/{daemonset_name}"
            notes.append("target inferred from daemonset labels")
        elif service_name:
            target = f"service/{service_name}"
            notes.append("target inferred from service labels")
        elif node_name:
            target = f"node/{node_name}"
            notes.append("target inferred from node labels")
        elif app_name:
            target = app_name
            notes.append("target inferred from app labels")
    if not target:
        raise ValueError("target could not be inferred from alert input")

    service_name = _first_non_empty(
        req.service_name,
        _label_value(labels, "service", "service_name", "app", "app_kubernetes_io_name", "job"),
    )
    node_name = req.node_name or _label_value(labels, "node", "node_name", "kubernetes_node", "instance")
    profile = req.profile
    if profile == "workload" and (target.startswith("service/") or service_name):
        profile = "service"
        notes.append("profile promoted to service based on target")

    scope = _scope_from_target(target, profile)
    if scope == "node" and not node_name and target.startswith("node/"):
        node_name = target.split("/", 1)[1]
    if scope != "node" and not namespace:
        raise ValueError("namespace could not be inferred from alert input")

    return NormalizedInvestigationRequest(
        source="alert",
        scope=scope,
        namespace=namespace,
        target=target,
        node_name=node_name if scope == "node" else None,
        service_name=service_name if scope == "service" else None,
        profile=profile,
        lookback_minutes=req.lookback_minutes or get_default_lookback_minutes(),
        normalization_notes=notes,
    )


def collect_workload_context(req: CollectContextRequest) -> CollectedContextResponse:
    return _collect_context(req)


def find_unhealthy_workloads(req: FindUnhealthyWorkloadsRequest) -> UnhealthyWorkloadsResponse:
    return find_unhealthy_workloads_impl(namespace=req.namespace, limit=req.limit)


def find_unhealthy_pod(req: FindUnhealthyPodRequest) -> UnhealthyPodResponse:
    workloads = find_unhealthy_workloads_impl(namespace=req.namespace, limit=1)
    candidate = workloads.candidates[0] if workloads.candidates else None
    limitations = list(workloads.limitations)
    if candidate is None:
        limitations.append("no unhealthy pod found in namespace")
    return UnhealthyPodResponse(
        namespace=req.namespace,
        candidate=candidate,
        limitations=sorted(set(limitations)),
    )


def collect_node_context(req: CollectNodeContextRequest) -> CollectedContextResponse:
    return _collect_context(
        CollectContextRequest(
            namespace=None,
            target=f"node/{req.node_name}",
            profile="workload",
            lookback_minutes=req.lookback_minutes,
        )
    )


def collect_service_context(req: CollectServiceContextRequest) -> CollectedContextResponse:
    return _collect_context(
        CollectContextRequest(
            namespace=req.namespace,
            target=req.target or f"service/{req.service_name}",
            profile="service",
            service_name=req.service_name,
            lookback_minutes=req.lookback_minutes,
        )
    )


def collect_alert_context(req: CollectAlertContextRequest) -> CollectedContextResponse:
    normalized = normalize_alert_input(req)
    if normalized.scope == "node" and normalized.node_name:
        context = collect_node_context(
            CollectNodeContextRequest(
                node_name=normalized.node_name,
                lookback_minutes=normalized.lookback_minutes,
            )
        )
    elif normalized.scope == "service" and normalized.service_name and normalized.namespace:
        context = collect_service_context(
            CollectServiceContextRequest(
                namespace=normalized.namespace,
                service_name=normalized.service_name,
                target=normalized.target,
                lookback_minutes=normalized.lookback_minutes,
            )
        )
    else:
        context = collect_workload_context(
            CollectContextRequest(
                namespace=normalized.namespace,
                target=normalized.target,
                profile=normalized.profile,
                service_name=normalized.service_name,
                lookback_minutes=normalized.lookback_minutes,
            )
        )

    limitations = list(context.limitations)
    limitations.append(f"alertname: {req.alertname}")
    if req.annotations:
        limitations.append("alert annotations supplied as investigation hints")
    enrichment_hints = sorted(set(context.enrichment_hints + ["normalization completed before collection"]))
    return context.model_copy(
        update={
            "limitations": sorted(set(limitations)),
            "enrichment_hints": enrichment_hints,
        }
    )


def build_root_cause_report(
    context: CollectedContextResponse, request: NormalizedInvestigationRequest | CollectContextRequest
) -> RootCauseReport:
    if isinstance(request, NormalizedInvestigationRequest):
        scope = request.scope
        profile = request.profile
    else:
        scope = _scope_from_target(request.target, request.profile)
        profile = request.profile

    critical = [item for item in context.findings if item.severity == "critical"]
    warnings = [item for item in context.findings if item.severity == "warning"]
    lead = critical[0] if critical else warnings[0] if warnings else context.findings[0]
    confidence = "high" if critical else "medium" if warnings else "low"

    evidence = [f"{item.source}: {item.title} - {item.evidence}" for item in context.findings[:5]]
    if context.events and context.events != ["no related events"]:
        evidence.append(f"recent events: {context.events[0]}")

    recommended_next_step = "Use Kubernetes describe/logs to confirm the failure before taking write actions."
    if scope == "service":
        recommended_next_step = "Check service dashboards, recent deploys, and upstream/downstream dependencies."
    elif scope == "node":
        recommended_next_step = "Inspect allocatable vs requests, top consumers, and recent node pressure events."
    elif profile == "otel-pipeline":
        recommended_next_step = "Verify collector ingestion, exporter health, and recent telemetry pipeline changes."

    likely_cause = None if lead.source == "heuristic" else lead.title
    suggested_follow_ups = list(context.enrichment_hints)
    return RootCauseReport(
        scope=scope,
        target=f"{context.target.kind}/{context.target.name}",
        diagnosis=lead.title,
        likely_cause=likely_cause,
        confidence=confidence,
        evidence=evidence,
        limitations=context.limitations,
        recommended_next_step=recommended_next_step,
        suggested_follow_ups=suggested_follow_ups,
    )
