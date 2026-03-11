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
PROM_URL="http://127.0.0.1:${PROM_PORT}"
LOOKBACK_MINUTES="${LOOKBACK_MINUTES:-1}"
SERVICE_SCOUT_SCENARIO="${SERVICE_SCOUT_SCENARIO:-weak_but_usable}"
PROMETHEUS_WAIT_ATTEMPTS="${PROMETHEUS_WAIT_ATTEMPTS:-36}"
PROMETHEUS_WAIT_SLEEP="${PROMETHEUS_WAIT_SLEEP:-5}"

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
    if python3 - "${PROM_URL}" <<'PY'
import sys
import urllib.request

url = f"{sys.argv[1]}/-/ready"
try:
    with urllib.request.urlopen(url, timeout=2) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
    then
      return 0
    fi
    sleep "${sleep_seconds}"
  done

  echo "Timed out waiting for Prometheus readiness" >&2
  exit 1
}

cleanup() {
  status=$?
  if kubectl --context "${KIND_CONTEXT}" -n kagent get deploy/prometheus-mcp-server >/dev/null 2>&1; then
    kubectl --context "${KIND_CONTEXT}" -n kagent logs deploy/prometheus-mcp-server >/tmp/kind-validate-service-scout-prom-mcp.log 2>&1 || true
  fi
  if [[ $status -ne 0 ]]; then
    echo "Service scout validation failed. Debug artifacts:"
    echo "  report: /tmp/kind-validate-service-scout-report.json"
    echo "  runtime logs: /tmp/kind-validate-service-scout-runtime.log"
    echo "  prometheus MCP logs: /tmp/kind-validate-service-scout-prom-mcp.log"
    echo "  prometheus port-forward log: /tmp/kind-validate-service-scout-prometheus.log"
    echo "  http port-forward log: /tmp/kind-validate-service-scout-http.log"
    if [[ "${KEEP_CLUSTER}" == "1" ]]; then
      echo "Cluster retained for debugging."
      echo "Useful commands:"
      echo "  kubectl --context ${KIND_CONTEXT} -n kagent get pods"
      echo "  kubectl --context ${KIND_CONTEXT} -n kagent logs deploy/prometheus-mcp-server"
      echo "  kubectl --context ${KIND_CONTEXT} -n kagent logs deploy/investigation-service"
      echo "  kubectl --context ${KIND_CONTEXT} -n ${SMOKE_NAMESPACE} get pods,svc"
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

if [[ "${SERVICE_SCOUT_SCENARIO}" != "weak_but_usable" ]]; then
  echo "Initial service scout validation lane currently supports only SERVICE_SCOUT_SCENARIO=weak_but_usable" >&2
  exit 1
fi

if [[ "${KEEP_CLUSTER}" == "0" ]] && kind get clusters | grep -qx "${KIND_CLUSTER_NAME}"; then
  echo "==> Removing existing kind cluster for deterministic Prometheus history"
  run_make kind-down
fi

echo "==> Setting up local kind stack"
run_make kind-setup

echo "==> Enabling HTTP validation overlay"
run_make kind-enable-http-debug
kubectl --context "${KIND_CONTEXT}" -n kagent rollout restart deploy/investigation-service
kubectl --context "${KIND_CONTEXT}" -n kagent rollout status deploy/investigation-service --timeout=180s

echo "==> Building and loading metrics smoke image"
run_make kind-build-metrics-smoke-image
run_make kind-load-metrics-smoke-image

echo "==> Applying metrics smoke workload in ${SERVICE_SCOUT_SCENARIO} mode"
METRICS_SMOKE_SCENARIO="${SERVICE_SCOUT_SCENARIO}" run_make metrics-smoke-apply
kubectl --context "${KIND_CONTEXT}" -n "${SMOKE_NAMESPACE}" rollout restart deploy/metrics-api
kubectl --context "${KIND_CONTEXT}" -n "${SMOKE_NAMESPACE}" rollout status deploy/metrics-api --timeout=180s
kubectl --context "${KIND_CONTEXT}" -n "${SMOKE_NAMESPACE}" rollout status deploy/metrics-api-traffic --timeout=180s

echo "==> Waiting for in-cluster services"
kubectl --context "${KIND_CONTEXT}" -n kagent rollout status deploy/prometheus --timeout=180s
kubectl --context "${KIND_CONTEXT}" -n kagent rollout status deploy/investigation-service --timeout=180s

echo "==> Port-forwarding Prometheus and investigation service"
kubectl --context "${KIND_CONTEXT}" -n kagent port-forward svc/prometheus "${PROM_PORT}:9090" >/tmp/kind-validate-service-scout-prometheus.log 2>&1 &
prom_port_forward_pid=$!
kubectl --context "${KIND_CONTEXT}" -n kagent port-forward svc/investigation-service "${HTTP_PORT}:8080" >/tmp/kind-validate-service-scout-http.log 2>&1 &
http_port_forward_pid=$!
wait_for_prometheus_ready
wait_for_http_ready

echo "==> Waiting for weak-but-usable Prometheus state"
PYTHONPATH="${ROOT_DIR}/src" uv run python - "${PROM_URL}" "${SMOKE_NAMESPACE}" "metrics-api" "${LOOKBACK_MINUTES}" "${PROMETHEUS_WAIT_ATTEMPTS}" "${PROMETHEUS_WAIT_SLEEP}" <<'PY'
import json
import sys
import time
import urllib.parse
import urllib.request

from investigation_service.prom_adapter import service_metric_query_families

prom_url, namespace, service_name, lookback_minutes = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
attempts, sleep_seconds = int(sys.argv[5]), int(sys.argv[6])


def query(query_string: str) -> float | None:
    url = f"{prom_url}/api/v1/query?{urllib.parse.urlencode({'query': query_string})}"
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


def query_range(query_string: str) -> list[float]:
    end = time.time()
    start = end - 180
    params = urllib.parse.urlencode(
        {
            "query": query_string,
            "start": f"{start:.0f}",
            "end": f"{end:.0f}",
            "step": "15",
        }
    )
    url = f"{prom_url}/api/v1/query_range?{params}"
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("status") != "success":
        return []
    result = payload.get("data", {}).get("result", [])
    samples: list[float] = []
    for series in result:
        for item in series.get("values", []):
            if len(item) < 2 or item[1] in {"NaN", "Inf", "-Inf"}:
                continue
            try:
                samples.append(float(item[1]))
            except ValueError:
                continue
    return samples


for _ in range(attempts):
    family_summaries = []
    for family_id, queries in service_metric_query_families(namespace, service_name, lookback_minutes):
        instant = {label: query(query_string) for label, query_string in queries.items()}
        historical = {label: query_range(query_string) for label, query_string in queries.items()}
        family_summaries.append((family_id, instant, historical))

    for family_id, instant, historical in family_summaries:
        instant_present = sum(value is not None for value in instant.values())
        request_history = historical["service_request_rate"]
        error_history = historical["service_error_rate"]
        latency_history = historical["service_latency_p95_seconds"]
        if (
            instant_present == 0
            and request_history
            and max(request_history) > 0
            and error_history
            and max(error_history) > 0
            and latency_history
            and max(latency_history) > 1
        ):
            print(f"Recovered historical metrics via family {family_id}")
            raise SystemExit(0)

    time.sleep(sleep_seconds)

raise SystemExit("weak-but-usable Prometheus state never materialized")
PY

echo "==> Capturing baseline workload bundle shape"
PROMETHEUS_URL="${PROM_URL}" CLUSTER_REGISTRY_PATH="" PYTHONPATH="${ROOT_DIR}/src" uv run python - "${LOOKBACK_MINUTES}" <<'PY'
import sys

from investigation_service.adequacy import assess_workload_evidence_bundle
from investigation_service.models import CollectContextRequest
from investigation_service.tools import collect_workload_evidence

lookback_minutes = int(sys.argv[1])

bundle = collect_workload_evidence(
    CollectContextRequest(
        namespace="metrics-smoke",
        target="deployment/metrics-api",
        profile="workload",
        service_name="metrics-api",
        lookback_minutes=lookback_minutes,
    )
)
assessment = assess_workload_evidence_bundle(bundle=bundle)
print(f"Baseline workload adequacy: {assessment.outcome}")
print(f"Baseline workload reasons: {list(assessment.reasons)}")
PY

run_started_at="$(python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
PY
)"

echo "==> Running orchestrated investigation"
python3 - "${HTTP_PORT}" "${LOOKBACK_MINUTES}" <<'PY'
import json
import sys
import urllib.request

http_port = sys.argv[1]
lookback_minutes = int(sys.argv[2])
payload = {
    "namespace": "metrics-smoke",
    "target": "deployment/metrics-api",
    "profile": "workload",
    "service_name": "metrics-api",
    "lookback_minutes": lookback_minutes,
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

with open("/tmp/kind-validate-service-scout-report.json", "w", encoding="utf-8") as handle:
    json.dump(report, handle, indent=2, sort_keys=True)

trace = report.get("tool_path_trace") or {}
assert trace.get("planner_path_used") is True, report
assert "batch-follow-up-service" in (trace.get("executed_batch_ids") or []), trace
step_provenance = trace.get("step_provenance") or []
follow_up_steps = [item for item in step_provenance if item.get("step_id") == "collect-service-follow-up-evidence"]
assert follow_up_steps, report
provenance = follow_up_steps[0]["provenance"]
assert provenance["requested_capability"] == "service_evidence_plane", provenance
attempted_servers = {route.get("mcp_server") for route in provenance.get("attempted_routes") or []}
assert "prometheus-mcp-server" in attempted_servers, provenance
actual_route = provenance.get("actual_route") or {}
actual_server = actual_route.get("mcp_server")
actual_tool = actual_route.get("tool_name")
route_satisfaction = provenance.get("route_satisfaction")
assert actual_server in {"prometheus-mcp-server", "kubernetes-mcp-server"}, provenance
if route_satisfaction == "preferred" and actual_server == "prometheus-mcp-server" and actual_tool == "execute_range_query":
    print("Scout outcome classification: probe_improved_artifact")
else:
    print(
        "Scout outcome classification: follow_up_executed_without_upgrade "
        f"(route_satisfaction={route_satisfaction}, actual_server={actual_server}, actual_tool={actual_tool})"
    )
PY

echo "==> Verifying bounded service scout runtime logs"
kubectl --context "${KIND_CONTEXT}" -n kagent logs deploy/investigation-service --since-time="${run_started_at}" > /tmp/kind-validate-service-scout-runtime.log
PYTHONPATH="${ROOT_DIR}/src" uv run python - /tmp/kind-validate-service-scout-runtime.log <<'PY'
import json
import sys

from investigation_service.execution_policy import bounded_exploration_policy_for_capability

marker = "orchestrator_bounded_scout summary="
policy = bounded_exploration_policy_for_capability("service_evidence_plane")
expected_metric_families = policy.max_metric_families if policy is not None else 0

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    summaries = []
    for line in handle:
        if marker not in line:
            continue
        payload = line.split(marker, 1)[1].strip()
        summaries.append(json.loads(payload))

service_entries = [item for item in summaries if item.get("capability") == "service_evidence_plane"]
assert service_entries, summaries
matching_entries = [
    item
    for item in service_entries
    if item.get("probe_kind") == "service_range_metrics"
    and item.get("metric_families_requested") == expected_metric_families
]
assert matching_entries, service_entries
stop_reasons = {item.get("stop_reason") for item in matching_entries}
if "probe_improved_artifact" in stop_reasons:
    print("Bounded scout stop_reason: probe_improved_artifact")
elif "probe_not_improving" in stop_reasons:
    print("Bounded scout stop_reason: probe_not_improving")
    raise SystemExit(
        "service follow-up executed and bounded scout attempted range metrics, but the artifact did not improve"
    )
elif "probe_failed" in stop_reasons:
    print("Bounded scout stop_reason: probe_failed")
    raise SystemExit("service follow-up executed but the bounded service scout probe failed")
else:
    raise SystemExit(f"unexpected bounded scout stop reasons: {sorted(stop_reasons)}")
PY

echo "==> Local bounded service scout validation passed"
