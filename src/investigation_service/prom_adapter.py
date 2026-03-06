import json
import urllib.parse
import urllib.request

from .settings import get_prometheus_url


def query_instant(query: str) -> float | None:
    base_url = get_prometheus_url()
    params = urllib.parse.urlencode({"query": query})
    url = f"{base_url}/api/v1/query?{params}"

    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("status") != "success":
        return None

    result = payload.get("data", {}).get("result", [])
    if not result:
        return None

    value = result[0].get("value")
    if not value or len(value) < 2:
        return None

    return float(value[1])


def collect_core_service_metrics() -> dict:
    return {
        "prometheus_url": get_prometheus_url(),
        "accepted_spans_per_sec": query_instant("sum(rate(otelcol_receiver_accepted_spans_total[1m]))"),
        "accepted_logs_per_sec": query_instant("sum(rate(otelcol_receiver_accepted_log_records_total[1m]))"),
        "accepted_metric_points_per_sec": query_instant(
            "sum(rate(otelcol_receiver_accepted_metric_points_total[1m]))"
        ),
        "up_targets": query_instant("sum(up)"),
    }
