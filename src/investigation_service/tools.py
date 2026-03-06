from .analysis import derive_findings
from .k8s_adapter import get_k8s_object, get_pod_logs, get_related_events, resolve_runtime_target, resolve_target
from .models import CollectAlertContextRequest, CollectContextRequest, CollectedContextResponse
from .prom_adapter import collect_core_service_metrics
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
    import re

    for pattern, kind in patterns:
        match = re.search(pattern, lower)
        if match:
            return f"{kind}/{match.group(1)}"
    return None


def _infer_alert_inputs(req: CollectAlertContextRequest) -> CollectContextRequest:
    labels = req.labels
    annotations = req.annotations
    target = req.target
    if not target:
        target = _infer_target_from_text(
            _first_non_empty(
                _annotation_value(annotations, "summary", "description", "message"),
                labels.get("summary"),
            )
        )

    namespace = _first_non_empty(
        req.namespace,
        _label_value(labels, "namespace", "kubernetes_namespace", "exported_namespace"),
    )
    if not namespace and not (target and target.startswith("node/")):
        raise ValueError("namespace could not be inferred from alert input")

    if not target:
        pod_name = _label_value(labels, "pod", "pod_name", "kubernetes_pod_name")
        deployment_name = _label_value(labels, "deployment", "deployment_name", "kubernetes_deployment_name")
        statefulset_name = _label_value(labels, "statefulset", "statefulset_name", "kubernetes_statefulset_name")
        daemonset_name = _label_value(labels, "daemonset", "daemonset_name", "kubernetes_daemonset_name")
        service_name = _label_value(labels, "service", "service_name")
        node_name = _label_value(labels, "node", "node_name", "kubernetes_node", "instance")
        app_name = _label_value(labels, "app", "app_kubernetes_io_name", "job")
        if pod_name:
            target = f"pod/{pod_name}"
        elif deployment_name:
            target = f"deployment/{deployment_name}"
        elif statefulset_name:
            target = f"deployment/{statefulset_name}"
        elif daemonset_name:
            target = f"deployment/{daemonset_name}"
        elif service_name:
            target = f"service/{service_name}"
        elif node_name:
            target = f"node/{node_name}"
        elif app_name:
            target = app_name
    if not target:
        raise ValueError("target could not be inferred from alert input")

    service_name = _first_non_empty(
        req.service_name,
        _label_value(labels, "service", "service_name", "app", "app_kubernetes_io_name", "job"),
    )
    profile = req.profile
    if profile == "workload" and (target.startswith("service/") or service_name):
        profile = "service"

    return CollectContextRequest(
        namespace=namespace,
        target=target,
        profile=profile,
        service_name=service_name,
        lookback_minutes=req.lookback_minutes or get_default_lookback_minutes(),
    )


def collect_workload_context(req: CollectContextRequest) -> CollectedContextResponse:
    requested_target = resolve_target(req.namespace, req.target)
    target = resolve_runtime_target(requested_target)
    object_state = get_k8s_object(target)
    events = get_related_events(target)
    logs = get_pod_logs(target, tail=get_log_tail_lines())
    lookback_minutes = req.lookback_minutes or get_default_lookback_minutes()
    metrics, metric_limitations = collect_core_service_metrics(
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

    return CollectedContextResponse(
        target=target,
        object_state=object_state,
        events=events,
        log_excerpt=logs[:4000],
        metrics=metrics,
        findings=findings,
        limitations=sorted(set(limitations)),
    )


def collect_alert_context(req: CollectAlertContextRequest) -> CollectedContextResponse:
    normalized = _infer_alert_inputs(req)
    context = collect_workload_context(normalized)
    limitations = list(context.limitations)
    limitations.append(f"alertname: {req.alertname}")
    if req.annotations:
        limitations.append("alert annotations supplied as investigation hints")
    return context.model_copy(update={"limitations": sorted(set(limitations))})
