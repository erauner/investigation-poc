import json
import os
import urllib.parse
import urllib.request


def get_k8s_objects(namespace: str, target: str) -> dict:
    # Placeholder for kubectl client integration.
    return {"namespace": namespace, "target": target, "status": "unknown"}


def get_events(namespace: str, target: str) -> list[str]:
    # Placeholder for event lookup.
    return [f"No recent warning events found for {target} in {namespace}"]


def get_logs(namespace: str, target: str) -> str:
    # Placeholder for log collection.
    return "No logs collected yet."


def _prom_query(query: str) -> float | None:
    base_url = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
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


def query_prometheus(namespace: str, target: str) -> dict:
    # Use OTEL collector counters as a quick local signal-ingestion health view.
    try:
        return {
            "prometheus_url": os.getenv("PROMETHEUS_URL", "http://localhost:9090"),
            "accepted_spans_per_sec": _prom_query("sum(rate(otelcol_receiver_accepted_spans_total[1m]))"),
            "accepted_logs_per_sec": _prom_query("sum(rate(otelcol_receiver_accepted_log_records_total[1m]))"),
            "accepted_metric_points_per_sec": _prom_query(
                "sum(rate(otelcol_receiver_accepted_metric_points_total[1m]))"
            ),
            "up_targets": _prom_query("sum(up)"),
        }
    except Exception as exc:
        return {"error": f"prometheus query failed: {exc}"}
