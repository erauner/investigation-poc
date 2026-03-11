#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_NAMESPACE="${SMOKE_NAMESPACE:-metrics-smoke}"
KEEP_SMOKE="${KEEP_SMOKE:-0}"
KEEP_CLUSTER="${KEEP_CLUSTER:-0}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-investigation}"
KIND_CONTEXT="${KIND_CONTEXT:-kind-${KIND_CLUSTER_NAME}}"
HTTP_PORT="${HTTP_PORT:-18080}"
PROM_PORT="${PROM_PORT:-19091}"
LOKI_PORT="${LOKI_PORT:-13100}"
PROM_URL="http://127.0.0.1:${PROM_PORT}"
LOKI_URL="http://127.0.0.1:${LOKI_PORT}"
LOKI_SCENARIO="${LOKI_SCENARIO:-loki_complementary}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

run_make() {
  make -C "${ROOT_DIR}" "$@"
}

wait_for_http_ready() {
  local attempts="${1:-30}"
  local sleep_seconds="${2:-2}"
  for _ in $(seq 1 "${attempts}"); do
    if curl -s "http://127.0.0.1:${HTTP_PORT}/healthz" >/dev/null; then
      return 0
    fi
    sleep "${sleep_seconds}"
  done
  echo "Timed out waiting for investigation HTTP endpoint readiness" >&2
  exit 1
}

wait_for_prometheus_ready() {
  local attempts="${1:-30}"
  local sleep_seconds="${2:-2}"
  for _ in $(seq 1 "${attempts}"); do
    if curl -fsS "${PROM_URL}/-/ready" >/dev/null; then
      return 0
    fi
    sleep "${sleep_seconds}"
  done
  echo "Timed out waiting for Prometheus readiness" >&2
  exit 1
}

wait_for_loki_ready() {
  local attempts="${1:-30}"
  local sleep_seconds="${2:-2}"
  for _ in $(seq 1 "${attempts}"); do
    if curl -fsS "${LOKI_URL}/ready" >/dev/null; then
      return 0
    fi
    sleep "${sleep_seconds}"
  done
  echo "Timed out waiting for Loki readiness" >&2
  exit 1
}

cleanup() {
  status=$?
  if kubectl --context "${KIND_CONTEXT}" -n kagent get deploy/loki-mcp-server >/dev/null 2>&1; then
    kubectl --context "${KIND_CONTEXT}" -n kagent logs deploy/loki-mcp-server >/tmp/kind-validate-loki-complementary-loki-mcp.log 2>&1 || true
  fi
  if kubectl --context "${KIND_CONTEXT}" -n kagent get deploy/loki >/dev/null 2>&1; then
    kubectl --context "${KIND_CONTEXT}" -n kagent logs deploy/loki >/tmp/kind-validate-loki-complementary-loki.log 2>&1 || true
  fi
  if [[ $status -ne 0 ]]; then
    echo "Loki complementary validation failed. Debug artifacts:"
    echo "  report: /tmp/kind-validate-loki-complementary-report.json"
    echo "  runtime logs: /tmp/kind-validate-loki-complementary-runtime.log"
    echo "  loki logs: /tmp/kind-validate-loki-complementary-loki.log"
    echo "  loki MCP logs: /tmp/kind-validate-loki-complementary-loki-mcp.log"
    echo "  loki port-forward log: /tmp/kind-validate-loki-complementary-loki-portforward.log"
    if [[ "${KEEP_CLUSTER}" == "1" ]]; then
      echo "Cluster retained for debugging."
      echo "Useful commands:"
      echo "  kubectl --context ${KIND_CONTEXT} -n kagent get pods"
      echo "  kubectl --context ${KIND_CONTEXT} -n kagent logs deploy/loki"
      echo "  kubectl --context ${KIND_CONTEXT} -n kagent logs deploy/loki-mcp-server"
      echo "  kubectl --context ${KIND_CONTEXT} -n ${SMOKE_NAMESPACE} logs deploy/metrics-api"
    fi
  fi
  if [[ -n "${http_port_forward_pid:-}" ]]; then
    kill "${http_port_forward_pid}" >/dev/null 2>&1 || true
    wait "${http_port_forward_pid}" 2>/dev/null || true
  fi
  if [[ -n "${prom_port_forward_pid:-}" ]]; then
    kill "${prom_port_forward_pid}" >/dev/null 2>&1 || true
    wait "${prom_port_forward_pid}" 2>/dev/null || true
  fi
  if [[ -n "${loki_port_forward_pid:-}" ]]; then
    kill "${loki_port_forward_pid}" >/dev/null 2>&1 || true
    wait "${loki_port_forward_pid}" 2>/dev/null || true
  fi
  if [[ "${KEEP_SMOKE}" != "1" ]]; then
    run_make metrics-smoke-clean >/dev/null 2>&1 || true
  fi
  if [[ "${KEEP_CLUSTER}" == "0" ]]; then
    run_make kind-down >/dev/null 2>&1 || true
  fi
  return $status
}
trap cleanup EXIT

need_cmd kind
need_cmd kubectl
need_cmd helm
need_cmd curl
need_cmd docker
need_cmd python3

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required" >&2
  exit 1
fi

if [[ "${LOKI_SCENARIO}" != "loki_complementary" ]]; then
  echo "This validation lane currently supports only LOKI_SCENARIO=loki_complementary" >&2
  exit 1
fi

if [[ "${KEEP_CLUSTER}" == "0" ]] && kind get clusters | grep -qx "${KIND_CLUSTER_NAME}"; then
  echo "==> Removing existing kind cluster for deterministic Loki history"
  run_make kind-down
fi

echo "==> Setting up local kind stack"
run_make kind-setup

echo "==> Enabling HTTP validation overlay"
run_make kind-enable-http-debug

echo "==> Enabling Loki overlay"
run_make kind-enable-loki-debug

echo "==> Building and loading metrics smoke image"
run_make kind-build-metrics-smoke-image
run_make kind-load-metrics-smoke-image

echo "==> Applying metrics smoke workload in ${LOKI_SCENARIO} mode"
METRICS_SMOKE_SCENARIO="${LOKI_SCENARIO}" run_make metrics-smoke-apply
kubectl --context "${KIND_CONTEXT}" -n "${SMOKE_NAMESPACE}" rollout restart deploy/metrics-api
kubectl --context "${KIND_CONTEXT}" -n "${SMOKE_NAMESPACE}" rollout status deploy/metrics-api --timeout=180s
kubectl --context "${KIND_CONTEXT}" -n "${SMOKE_NAMESPACE}" rollout status deploy/metrics-api-traffic --timeout=180s

echo "==> Waiting for in-cluster services"
kubectl --context "${KIND_CONTEXT}" -n kagent rollout status deploy/prometheus --timeout=180s
kubectl --context "${KIND_CONTEXT}" -n kagent rollout status deploy/loki --timeout=180s
kubectl --context "${KIND_CONTEXT}" -n kagent rollout status daemonset/promtail --timeout=240s
kubectl --context "${KIND_CONTEXT}" -n kagent rollout status deploy/loki-mcp-server --timeout=240s
kubectl --context "${KIND_CONTEXT}" -n kagent rollout status deploy/investigation-service --timeout=180s

echo "==> Port-forwarding Prometheus, Loki, and investigation service"
kubectl --context "${KIND_CONTEXT}" -n kagent port-forward svc/prometheus "${PROM_PORT}:9090" >/tmp/kind-validate-loki-complementary-prometheus.log 2>&1 &
prom_port_forward_pid=$!
kubectl --context "${KIND_CONTEXT}" -n kagent port-forward svc/loki "${LOKI_PORT}:3100" >/tmp/kind-validate-loki-complementary-loki-portforward.log 2>&1 &
loki_port_forward_pid=$!
kubectl --context "${KIND_CONTEXT}" -n kagent port-forward svc/investigation-service "${HTTP_PORT}:8080" >/tmp/kind-validate-loki-complementary-http.log 2>&1 &
http_port_forward_pid=$!
wait_for_prometheus_ready
wait_for_loki_ready
wait_for_http_ready

echo "==> Waiting for metrics and logs to accumulate"
sleep 25

metrics_pod_name="$(
  kubectl --context "${KIND_CONTEXT}" -n "${SMOKE_NAMESPACE}" get pods -l app.kubernetes.io/name=metrics-api -o jsonpath='{.items[0].metadata.name}'
)"

echo "==> Verifying Prometheus service metrics"
python3 - "${PROM_URL}" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

base_url = sys.argv[1]
queries = {
    "request_rate": 'sum(rate(http_server_request_duration_seconds_count{namespace="metrics-smoke",service="metrics-api"}[5m]))',
    "error_rate": 'sum(rate(http_server_request_duration_seconds_count{namespace="metrics-smoke",service="metrics-api",status="500"}[5m]))',
    "latency_p95": 'histogram_quantile(0.95, sum by (le) (rate(http_server_request_duration_seconds_bucket{namespace="metrics-smoke",service="metrics-api"}[5m])))',
}
for label, query in queries.items():
    url = f"{base_url}/api/v1/query?{urllib.parse.urlencode({'query': query})}"
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = payload.get("data", {}).get("result", [])
    if not result:
        raise SystemExit(f"{label}: no Prometheus series returned")
    value = float(result[0]["value"][1])
    if value <= 0:
        raise SystemExit(f"{label}: expected positive value, got {value}")
    print(f"{label}: {value}")
PY

echo "==> Verifying Loki log ingestion"
python3 - "${LOKI_URL}" "${metrics_pod_name}" <<'PY'
import json
import sys
import time
import urllib.parse
import urllib.request

base_url, pod_name = sys.argv[1], sys.argv[2]
query = f'{{namespace="metrics-smoke",pod="{pod_name}"}}'
deadline = time.time() + 60
while time.time() < deadline:
    end_ns = time.time_ns()
    start_ns = end_ns - (300 * 1_000_000_000)
    params = urllib.parse.urlencode(
        {
            "query": query,
            "start": str(start_ns),
            "end": str(end_ns),
            "limit": 50,
        }
    )
    url = f"{base_url}/loki/api/v1/query_range?{params}"
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    results = payload.get("data", {}).get("result", [])
    lines = []
    for stream in results:
        for item in stream.get("values", []):
            if len(item) >= 2:
                lines.append(item[1])
    if any(
        ("stdout F error: upstream returned 500" in line)
        or ("stdout F exception: synthetic upstream timeout" in line)
        for line in lines
    ):
        print(f"recovered {len(lines)} Loki log lines for {pod_name}")
        raise SystemExit(0)
    time.sleep(2)
raise SystemExit("expected Loki error logs were not recovered")
PY

echo "==> Verifying direct push path is not configured"
fixture_path="${ROOT_DIR}/test-fixtures/metrics-smoke/overlays/loki_complementary/metrics-api.yaml"
if grep -Eq 'name:\s*(LOKI_PUSH_URL|LOKI_PUSH_ENDPOINT|OTEL_EXPORTER_OTLP_LOGS_ENDPOINT|OTEL_EXPORTER_OTLP_ENDPOINT)\b' "${fixture_path}"; then
  echo "unexpected direct-push env vars configured in ${fixture_path}" >&2
  exit 1
fi
echo "direct push env vars absent from loki_complementary fixture"

python3 - "${KIND_CONTEXT}" "${SMOKE_NAMESPACE}" <<'PY'
import json
import subprocess
import sys

context, namespace = sys.argv[1], sys.argv[2]
banned = {
    "LOKI_PUSH_URL",
    "LOKI_PUSH_ENDPOINT",
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
}

result = subprocess.run(
    [
        "kubectl",
        "--context",
        context,
        "-n",
        namespace,
        "get",
        "deploy",
        "metrics-api",
        "-o",
        "json",
    ],
    check=True,
    capture_output=True,
    text=True,
)
payload = json.loads(result.stdout)
containers = payload.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
present = sorted(
    {
        item.get("name")
        for container in containers
        for item in container.get("env", []) or []
        if item.get("name") in banned
    }
)
if present:
    raise SystemExit(f"unexpected direct-push env vars present in live metrics-api deployment: {', '.join(present)}")
print("direct push env vars absent from live metrics-api deployment")
PY

run_started_at="$(python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
PY
)"

echo "==> Running orchestrated investigation"
python3 - "${HTTP_PORT}" <<'PY'
import json
import sys
import urllib.request

http_port = sys.argv[1]
payload = {
    "namespace": "metrics-smoke",
    "target": "service/metrics-api",
    "profile": "service",
    "service_name": "metrics-api",
    "lookback_minutes": 15,
    "include_related_data": False,
}
req = urllib.request.Request(
    f"http://127.0.0.1:{http_port}/tools/run_orchestrated_investigation",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=60) as response:
    report = json.loads(response.read().decode("utf-8"))

with open("/tmp/kind-validate-loki-complementary-report.json", "w", encoding="utf-8") as handle:
    json.dump(report, handle, indent=2, sort_keys=True)

trace = report.get("tool_path_trace") or {}
assert trace.get("planner_path_used") is True, report
step_provenance = trace.get("step_provenance") or []
target_steps = [item for item in step_provenance if item.get("step_id") == "collect-target-evidence"]
assert target_steps, report
provenance = target_steps[0]["provenance"]
assert provenance["requested_capability"] == "service_evidence_plane", provenance
actual_route = provenance.get("actual_route") or {}
assert actual_route.get("mcp_server") == "prometheus-mcp-server", provenance
assert actual_route.get("tool_name") == "execute_query", provenance
contributing_servers = {route.get("mcp_server") for route in provenance.get("contributing_routes") or []}
assert "loki-mcp-server" in contributing_servers, provenance
print("Loki contributing route observed without replacing Prometheus actual_route")
PY

echo "==> Capturing runtime logs"
kubectl --context "${KIND_CONTEXT}" -n kagent logs deploy/investigation-service --since-time="${run_started_at}" >/tmp/kind-validate-loki-complementary-runtime.log

echo "==> Local Loki complementary validation passed"
