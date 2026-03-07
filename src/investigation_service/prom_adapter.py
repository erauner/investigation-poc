import json
import urllib.parse
import urllib.request
from urllib.error import URLError

from .cluster_registry import ResolvedCluster
from .models import TargetRef
from .settings import get_prometheus_url

_METRIC_METADATA_KEYS = {"profile", "prometheus_url", "lookback_minutes", "prometheus_available"}


def _effective_prometheus_url(cluster: ResolvedCluster | None) -> str:
    if cluster and cluster.prometheus_url:
        return cluster.prometheus_url
    return get_prometheus_url()


def query_instant(query: str, prometheus_url: str | None = None) -> float | None:
    prometheus_url = prometheus_url or get_prometheus_url()
    params = urllib.parse.urlencode({"query": query})
    url = f"{prometheus_url}/api/v1/query?{params}"

    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return None

    if payload.get("status") != "success":
        return None

    result = payload.get("data", {}).get("result", [])
    if not result:
        return None

    value = result[0].get("value")
    if not value or len(value) < 2:
        return None

    return float(value[1])


def _safe_metric(query: str, limitations: list[str], label: str, prometheus_url: str) -> float | None:
    try:
        value = query_instant(query, prometheus_url)
    except TypeError:
        value = query_instant(query)
    if value is None:
        limitations.append(f"metric unavailable: {label}")
    return value


def collect_global_otel_metrics(
    lookback_minutes: int,
    *,
    prometheus_url: str,
) -> tuple[dict, list[str]]:
    lookback = f"{max(lookback_minutes, 1)}m"
    limitations: list[str] = []
    metrics = {
        "prometheus_url": prometheus_url,
        "lookback_minutes": max(lookback_minutes, 1),
        "accepted_spans_per_sec": _safe_metric(
            f"sum(rate(otelcol_receiver_accepted_spans_total[{lookback}]))",
            limitations,
            "accepted_spans_per_sec",
            prometheus_url,
        ),
        "accepted_logs_per_sec": _safe_metric(
            f"sum(rate(otelcol_receiver_accepted_log_records_total[{lookback}]))",
            limitations,
            "accepted_logs_per_sec",
            prometheus_url,
        ),
        "accepted_metric_points_per_sec": _safe_metric(
            f"sum(rate(otelcol_receiver_accepted_metric_points_total[{lookback}]))",
            limitations,
            "accepted_metric_points_per_sec",
            prometheus_url,
        ),
        "up_targets": _safe_metric("sum(up)", limitations, "up_targets", prometheus_url),
    }
    return metrics, limitations


def collect_workload_metrics(
    target: TargetRef,
    lookback_minutes: int,
    *,
    prometheus_url: str,
) -> tuple[dict, list[str]]:
    lookback = f"{max(lookback_minutes, 1)}m"
    escaped_ns = (target.namespace or "").replace('"', '\\"')
    escaped_name = target.name.replace('"', '\\"')
    limitations: list[str] = []
    metrics = {
        "pod_restart_rate": _safe_metric(
            (
                f'sum(rate(kube_pod_container_status_restarts_total{{namespace="{escaped_ns}",pod=~"{escaped_name}.*"}}'
                f"[{lookback}]))"
            ),
            limitations,
            "pod_restart_rate",
            prometheus_url,
        ),
        "pod_cpu_cores": _safe_metric(
            (
                f'sum(rate(container_cpu_usage_seconds_total{{namespace="{escaped_ns}",pod=~"{escaped_name}.*"}}'
                f"[{lookback}]))"
            ),
            limitations,
            "pod_cpu_cores",
            prometheus_url,
        ),
        "pod_memory_working_set_bytes": _safe_metric(
            f'sum(container_memory_working_set_bytes{{namespace="{escaped_ns}",pod=~"{escaped_name}.*"}})',
            limitations,
            "pod_memory_working_set_bytes",
            prometheus_url,
        ),
    }
    return metrics, limitations


def collect_service_metrics(
    namespace: str,
    service_name: str,
    lookback_minutes: int,
    *,
    prometheus_url: str,
) -> tuple[dict, list[str]]:
    lookback = f"{max(lookback_minutes, 1)}m"
    escaped_ns = namespace.replace('"', '\\"')
    effective_service = service_name.replace('"', '\\"')
    limitations: list[str] = []
    metrics = {
        "service_request_rate": _safe_metric(
            (
                f'sum(rate(http_server_request_duration_seconds_count{{namespace="{escaped_ns}",service="{effective_service}"}}'
                f"[{lookback}]))"
            ),
            limitations,
            "service_request_rate",
            prometheus_url,
        ),
        "service_error_rate": _safe_metric(
            (
                "sum(rate(http_server_request_duration_seconds_count"
                f'{{namespace="{escaped_ns}",service="{effective_service}",status=~"5.."}}[{lookback}]))'
            ),
            limitations,
            "service_error_rate",
            prometheus_url,
        ),
        "service_latency_p95_seconds": _safe_metric(
            (
                "histogram_quantile(0.95, "
                "sum by (le) (rate(http_server_request_duration_seconds_bucket"
                f'{{namespace="{escaped_ns}",service="{effective_service}"}}[{lookback}])))'
            ),
            limitations,
            "service_latency_p95_seconds",
            prometheus_url,
        ),
    }
    return metrics, limitations


def collect_service_enrichment_metrics(
    namespace: str,
    service_name: str,
    lookback_minutes: int,
    cluster: ResolvedCluster | None = None,
) -> tuple[dict, list[str]]:
    prometheus_url = _effective_prometheus_url(cluster)
    return collect_service_metrics(
        namespace=namespace,
        service_name=service_name,
        lookback_minutes=lookback_minutes,
        prometheus_url=prometheus_url,
    )


def collect_node_metrics(node_name: str, *, prometheus_url: str) -> tuple[dict, list[str]]:
    escaped_name = node_name.replace('"', '\\"')
    limitations: list[str] = []
    metrics = {
        "node_memory_allocatable_bytes": _safe_metric(
            f'kube_node_status_allocatable{{node="{escaped_name}",resource="memory",unit="byte"}}',
            limitations,
            "node_memory_allocatable_bytes",
            prometheus_url,
        ),
        "node_memory_working_set_bytes": _safe_metric(
            f'sum(container_memory_working_set_bytes{{node="{escaped_name}",container!="",pod!=""}})',
            limitations,
            "node_memory_working_set_bytes",
            prometheus_url,
        ),
        "node_memory_request_bytes": _safe_metric(
            f'sum(kube_pod_container_resource_requests{{node="{escaped_name}",resource="memory",unit="byte"}})',
            limitations,
            "node_memory_request_bytes",
            prometheus_url,
        ),
    }
    return metrics, limitations


def collect_metrics_for_scope(
    target: TargetRef,
    profile: str,
    service_name: str | None,
    lookback_minutes: int,
    cluster: ResolvedCluster | None = None,
) -> tuple[dict, list[str]]:
    prometheus_url = _effective_prometheus_url(cluster)
    metrics = {
        "profile": profile,
        "prometheus_url": prometheus_url,
        "lookback_minutes": max(lookback_minutes, 1),
    }
    limitations: list[str] = []

    if profile == "otel-pipeline":
        otel_metrics, otel_limitations = collect_global_otel_metrics(
            lookback_minutes,
            prometheus_url=prometheus_url,
        )
        metrics.update(otel_metrics)
        limitations.extend(otel_limitations)

    scoped_metrics: dict = {}
    scoped_limitations: list[str] = []
    if target.kind == "node":
        scoped_metrics, scoped_limitations = collect_node_metrics(target.name, prometheus_url=prometheus_url)
    elif target.kind == "service" or profile == "service":
        scoped_metrics, scoped_limitations = collect_service_metrics(
            namespace=target.namespace or "",
            service_name=service_name or target.name,
            lookback_minutes=lookback_minutes,
            prometheus_url=prometheus_url,
        )
    elif profile == "workload":
        scoped_metrics, scoped_limitations = collect_workload_metrics(
            target,
            lookback_minutes,
            prometheus_url=prometheus_url,
        )

    metrics.update(scoped_metrics)
    limitations.extend(scoped_limitations)
    metrics["prometheus_available"] = any(
        value is not None for key, value in metrics.items() if key not in _METRIC_METADATA_KEYS
    )
    if not metrics["prometheus_available"]:
        limitations.append("prometheus unavailable or returned no usable results")
    return metrics, limitations


def collect_core_service_metrics(
    target: TargetRef,
    profile: str,
    service_name: str | None,
    lookback_minutes: int,
    cluster: ResolvedCluster | None = None,
) -> tuple[dict, list[str]]:
    return collect_metrics_for_scope(
        target=target,
        profile=profile,
        service_name=service_name,
        lookback_minutes=lookback_minutes,
        cluster=cluster,
    )
