#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_NAMESPACE="${SMOKE_NAMESPACE:-kagent-smoke}"
KEEP_SMOKE="${KEEP_SMOKE:-0}"
KEEP_CLUSTER="${KEEP_CLUSTER:-0}"
PROM_PORT="${PROM_PORT:-19090}"
PROM_URL="http://127.0.0.1:${PROM_PORT}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

run_make() {
  make -C "${ROOT_DIR}" "$@"
}

wait_for_unhealthy_pod() {
  local namespace="$1"
  local attempts="${2:-36}"
  local sleep_seconds="${3:-5}"

  for _ in $(seq 1 "${attempts}"); do
    if kubectl -n "${namespace}" get pods 2>/dev/null | grep -Eq 'CrashLoopBackOff|Error|Failed'; then
      return 0
    fi
    sleep "${sleep_seconds}"
  done

  echo "Timed out waiting for an unhealthy pod in namespace ${namespace}" >&2
  kubectl -n "${namespace}" get pods >&2 || true
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

require_prom_query() {
  local label="$1"
  local query="$2"
  python3 - "${PROM_URL}" "${label}" "${query}" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

base_url, label, query = sys.argv[1], sys.argv[2], sys.argv[3]
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
if numeric <= 0:
    raise SystemExit(f"{label}: expected value > 0, got {numeric}")

print(f"{label}: {numeric}")
PY
}

cleanup() {
  if [[ -n "${port_forward_pid:-}" ]]; then
    kill "${port_forward_pid}" >/dev/null 2>&1 || true
    wait "${port_forward_pid}" 2>/dev/null || true
  fi
  if [[ "${KEEP_SMOKE}" != "1" ]]; then
    run_make kagent-smoke-clean >/dev/null 2>&1 || true
  fi
  if [[ "${KEEP_CLUSTER}" == "0" ]]; then
    run_make kind-down >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

need_cmd kind
need_cmd kubectl
need_cmd helm
need_cmd python3

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required" >&2
  exit 1
fi

echo "==> Setting up local kind stack"
run_make kind-setup

echo "==> Waiting for in-cluster monitoring components"
kubectl -n kagent rollout status deploy/prometheus --timeout=180s
kubectl -n kagent rollout status deploy/kube-state-metrics --timeout=180s
kubectl -n kagent get pods -l app.kubernetes.io/name=prometheus
kubectl -n kagent get pods -l app.kubernetes.io/name=kube-state-metrics

echo "==> Applying smoke workload"
run_make kagent-smoke-apply
wait_for_unhealthy_pod "${SMOKE_NAMESPACE}"

echo "==> Port-forwarding Prometheus"
kubectl -n kagent port-forward svc/prometheus "${PROM_PORT}:9090" >/tmp/kind-validate-metrics-prometheus.log 2>&1 &
port_forward_pid=$!
wait_for_prometheus_ready

echo "==> Waiting for Prometheus to scrape workload metrics"
sleep 20

require_prom_query \
  "restart metrics" \
  'count(kube_pod_container_status_restarts_total{namespace="kagent-smoke",pod=~"crashy.*"})'
require_prom_query \
  "memory metrics" \
  'count(container_memory_working_set_bytes{namespace="kagent-smoke",pod=~"whoami.*",container!="",image!=""})'
require_prom_query \
  "cpu metrics" \
  'count(container_cpu_usage_seconds_total{namespace="kagent-smoke",pod=~"whoami.*",container!="",image!=""})'

echo "==> Local kind metrics validation passed"
