from .analysis import derive_findings
from .k8s_adapter import get_k8s_object, get_pod_logs, get_related_events, resolve_runtime_target, resolve_target
from .models import CollectContextRequest, CollectedContextResponse
from .prom_adapter import collect_core_service_metrics
from .settings import get_default_lookback_minutes, get_log_tail_lines


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
