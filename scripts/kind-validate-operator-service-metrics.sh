#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_NAMESPACE="${SMOKE_NAMESPACE:-operator-metrics-smoke}"
KEEP_SMOKE="${KEEP_SMOKE:-0}"
KEEP_CLUSTER="${KEEP_CLUSTER:-0}"
PROM_PORT="${PROM_PORT:-19092}"
PROM_URL="http://127.0.0.1:${PROM_PORT}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

run_make() {
  make -C "${ROOT_DIR}" "$@"
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

wait_for_cluster_statuses() {
  local attempts="${1:-36}"
  local sleep_seconds="${2:-5}"

  for _ in $(seq 1 "${attempts}"); do
    if kubectl -n "${SMOKE_NAMESPACE}" get cluster tenant-a -o jsonpath='{.status.componentStatuses[0].name}' 2>/dev/null | grep -q '.'; then
      return 0
    fi
    sleep "${sleep_seconds}"
  done

  echo "Timed out waiting for cluster componentStatuses in namespace ${SMOKE_NAMESPACE}" >&2
  kubectl -n "${SMOKE_NAMESPACE}" get cluster tenant-a -o yaml >&2 || true
  exit 1
}

wait_for_deployment() {
  local deployment_name="$1"
  local attempts="${2:-36}"
  local sleep_seconds="${3:-5}"

  for _ in $(seq 1 "${attempts}"); do
    if kubectl -n "${SMOKE_NAMESPACE}" get deployment "${deployment_name}" >/dev/null 2>&1; then
      return 0
    fi
    sleep "${sleep_seconds}"
  done

  echo "Timed out waiting for deployment/${deployment_name} in namespace ${SMOKE_NAMESPACE}" >&2
  kubectl -n "${SMOKE_NAMESPACE}" get backends,deployments,services,pods >&2 || true
  exit 1
}

require_prom_query() {
  local label="$1"
  local query="$2"
  local minimum="${3:-0}"
  python3 - "${PROM_URL}" "${label}" "${query}" "${minimum}" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

base_url, label, query, minimum = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
url = f"{base_url}/api/v1/query?{urllib.parse.urlencode({'query': query})}"
with urllib.request.urlopen(url, timeout=5) as response:
    payload = json.loads(response.read().decode("utf-8"))

if payload.get("status") != "success":
    raise SystemExit(f"{label}: Prometheus query failed")

result = payload.get("data", {}).get("result", [])
if not result:
    raise SystemExit(f"{label}: Prometheus query returned no series")

value = result[0].get("value")
if not value or len(value) < 2:
    raise SystemExit(f"{label}: Prometheus query returned no scalar value")

numeric = float(value[1])
if numeric <= minimum:
    raise SystemExit(f"{label}: expected value > {minimum}, got {numeric}")

print(f"{label}: {numeric}")
PY
}

cleanup() {
  if [[ -n "${port_forward_pid:-}" ]]; then
    kill "${port_forward_pid}" >/dev/null 2>&1 || true
    wait "${port_forward_pid}" 2>/dev/null || true
  fi
  if [[ "${KEEP_SMOKE}" != "1" ]]; then
    run_make operator-metrics-smoke-clean >/dev/null 2>&1 || true
  fi
  if [[ "${KEEP_CLUSTER}" == "0" ]]; then
    run_make kind-down >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

need_cmd kind
need_cmd kubectl
need_cmd helm
need_cmd docker
need_cmd python3
need_cmd uv

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required" >&2
  exit 1
fi

echo "==> Setting up local kind stack"
run_make kind-setup

echo "==> Installing homelab-operator into the local kind cluster"
run_make kind-install-operator

echo "==> Building and loading metrics smoke image"
run_make kind-build-metrics-smoke-image
run_make kind-load-metrics-smoke-image

echo "==> Applying operator metrics smoke workload"
run_make operator-metrics-smoke-apply
kubectl -n "${SMOKE_NAMESPACE}" rollout status deploy/api-traffic --timeout=180s
wait_for_deployment api
kubectl -n "${SMOKE_NAMESPACE}" rollout status deploy/api --timeout=180s
wait_for_cluster_statuses

echo "==> Port-forwarding Prometheus"
kubectl -n kagent port-forward svc/prometheus "${PROM_PORT}:9090" >/tmp/kind-validate-operator-service-metrics-prometheus.log 2>&1 &
port_forward_pid=$!
wait_for_prometheus_ready

echo "==> Waiting for service metrics to accumulate"
sleep 30

require_prom_query \
  "operator service request rate" \
  'sum(rate(http_server_request_duration_seconds_count{namespace="operator-metrics-smoke",service="api"}[5m]))'
require_prom_query \
  "operator service error rate" \
  'sum(rate(http_server_request_duration_seconds_count{namespace="operator-metrics-smoke",service="api",status="500"}[5m]))'
require_prom_query \
  "operator service latency p95" \
  'histogram_quantile(0.95, sum by (le) (rate(http_server_request_duration_seconds_bucket{namespace="operator-metrics-smoke",service="api"}[5m])))' \
  "1"

echo "==> Validating operator-backed runtime enrichment path"
PROMETHEUS_URL="${PROM_URL}" CLUSTER_REGISTRY_PATH="" PYTHONPATH="${ROOT_DIR}/src" uv run python - <<'PY'
from investigation_service.models import InvestigationReportRequest
from investigation_service.reporting import (
    _collect_context_for_normalized_request,
    _normalized_request,
    _resolve_backend_convenience_target,
    _resolve_cluster_convenience_target,
)

backend_normalized = _resolve_backend_convenience_target(
    _normalized_request(
        InvestigationReportRequest(
            namespace="operator-metrics-smoke",
            target="Backend/api",
            include_related_data=False,
        )
    )
)
assert backend_normalized.target == "deployment/api", backend_normalized
assert backend_normalized.service_name == "api", backend_normalized

backend_context = _collect_context_for_normalized_request(backend_normalized)
assert backend_context.metrics.get("service_request_rate"), backend_context.metrics
assert backend_context.metrics.get("service_error_rate"), backend_context.metrics
assert backend_context.metrics.get("service_latency_p95_seconds"), backend_context.metrics
backend_titles = {item.title for item in backend_context.findings}
assert "Service Returning 5xx Responses" in backend_titles, backend_titles
assert "High Service Latency" in backend_titles, backend_titles

cluster_normalized = _resolve_cluster_convenience_target(
    _normalized_request(
        InvestigationReportRequest(
            namespace="operator-metrics-smoke",
            target="Cluster/tenant-a",
            include_related_data=False,
        )
    )
)
assert cluster_normalized.target == "deployment/api", cluster_normalized
assert cluster_normalized.service_name == "api", cluster_normalized
assert any("resolved Cluster/tenant-a to failing component Backend/api" in note for note in cluster_normalized.normalization_notes), cluster_normalized.normalization_notes

cluster_context = _collect_context_for_normalized_request(cluster_normalized)
assert cluster_context.metrics.get("service_request_rate"), cluster_context.metrics
assert cluster_context.metrics.get("service_error_rate"), cluster_context.metrics
assert cluster_context.metrics.get("service_latency_p95_seconds"), cluster_context.metrics
cluster_titles = {item.title for item in cluster_context.findings}
assert "Service Returning 5xx Responses" in cluster_titles, cluster_titles
assert "High Service Latency" in cluster_titles, cluster_titles
PY

echo "==> Operator-backed service metrics validation passed"
