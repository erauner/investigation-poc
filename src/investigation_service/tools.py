from .analysis import derive_findings
from .k8s_adapter import get_k8s_object, get_pod_logs, get_related_events, resolve_runtime_target, resolve_target
from .models import CollectContextRequest, CollectedContextResponse
from .prom_adapter import collect_core_service_metrics
from .settings import get_log_tail_lines


def collect_workload_context(req: CollectContextRequest) -> CollectedContextResponse:
    requested_target = resolve_target(req.namespace, req.target)
    target = resolve_runtime_target(requested_target)
    object_state = get_k8s_object(target)
    events = get_related_events(target)
    logs = get_pod_logs(target, tail=get_log_tail_lines())
    metrics = collect_core_service_metrics()
    findings = derive_findings(object_state, events, logs, metrics)

    return CollectedContextResponse(
        target=target,
        object_state=object_state,
        events=events,
        log_excerpt=logs[:4000],
        metrics=metrics,
        findings=findings,
    )
