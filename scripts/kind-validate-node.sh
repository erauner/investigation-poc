#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_NAMESPACE="${SMOKE_NAMESPACE:-kagent-smoke}"
KEEP_SMOKE="${KEEP_SMOKE:-0}"
KEEP_CLUSTER="${KEEP_CLUSTER:-0}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-investigation}"
KIND_CONTEXT="${KIND_CONTEXT:-kind-${KIND_CLUSTER_NAME}}"
HTTP_PORT="${HTTP_PORT:-18080}"
CLUSTER_PREEXISTED=0

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

run_make() {
  make -C "${ROOT_DIR}" "$@"
}

kind_stack_is_reusable() {
  kind get clusters | grep -qx "${KIND_CLUSTER_NAME}" || return 1
  kubectl config get-contexts "${KIND_CONTEXT}" >/dev/null 2>&1 || return 1
  kubectl --context "${KIND_CONTEXT}" -n kagent get deploy/investigation-mcp-server >/dev/null 2>&1 || return 1
  kubectl --context "${KIND_CONTEXT}" -n kagent get deploy/investigation-service >/dev/null 2>&1 || return 1
  kubectl --context "${KIND_CONTEXT}" -n kagent get deploy/prometheus >/dev/null 2>&1 || return 1
  kubectl --context "${KIND_CONTEXT}" -n kagent get deploy/kube-state-metrics >/dev/null 2>&1 || return 1
}

ensure_kind_stack() {
  if kind get clusters | grep -qx "${KIND_CLUSTER_NAME}"; then
    CLUSTER_PREEXISTED=1
  fi

  if kind_stack_is_reusable; then
    echo "==> Reusing existing kind stack"
    kubectl config use-context "${KIND_CONTEXT}" >/dev/null
    kubectl get nodes
    return 0
  fi

  echo "==> Setting up local kind stack"
  run_make kind-up
  run_make kind-build-investigation-image
  run_make kind-load-investigation-image
  kubectl create namespace kagent --dry-run=client -o yaml | kubectl apply -f -
  helm upgrade --install kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds --version "${KAGENT_VERSION:-0.7.23}" -n kagent
  helm upgrade --install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent --version "${KAGENT_VERSION:-0.7.23}" -n kagent
  kubectl -n kagent create secret generic kagent-openai \
    --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY}" \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl apply -k "${ROOT_DIR}/k8s-overlays/local-kind"
  kubectl apply -k "${ROOT_DIR}/k8s-overlays/local-kind-optional-http"
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

cleanup() {
  if [[ -n "${port_forward_pid:-}" ]]; then
    kill "${port_forward_pid}" >/dev/null 2>&1 || true
    wait "${port_forward_pid}" 2>/dev/null || true
  fi
  if [[ "${KEEP_SMOKE}" != "1" ]]; then
    run_make kagent-smoke-clean >/dev/null 2>&1 || true
  fi
  if [[ "${KEEP_CLUSTER}" == "0" && "${CLUSTER_PREEXISTED}" == "0" ]]; then
    run_make kind-down >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

need_cmd kind
need_cmd kubectl
need_cmd helm
need_cmd curl
need_cmd python3

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required" >&2
  exit 1
fi

ensure_kind_stack

echo "==> Waiting for in-cluster monitoring components"
kubectl -n kagent rollout status deploy/prometheus --timeout=180s
kubectl -n kagent rollout status deploy/kube-state-metrics --timeout=180s
kubectl -n kagent rollout status deploy/investigation-mcp-server --timeout=180s
kubectl -n kagent rollout status deploy/investigation-service --timeout=180s

echo "==> Applying smoke workload"
run_make kagent-smoke-apply
wait_for_unhealthy_pod "${SMOKE_NAMESPACE}"

node_name="$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')"
if [[ -z "${node_name}" ]]; then
  echo "Failed to resolve a node name for validation" >&2
  exit 1
fi
echo "==> Validating node investigation for ${node_name}"

kubectl -n kagent port-forward svc/investigation-service "${HTTP_PORT}:8080" >/tmp/kind-validate-node-http.log 2>&1 &
port_forward_pid=$!
wait_for_http_ready

python3 - "${node_name}" "${HTTP_PORT}" <<'PY'
import json
import sys
import urllib.request

node_name = sys.argv[1]
http_port = sys.argv[2]
payload = {
    "target": f"node/{node_name}",
    "lookback_minutes": 15,
    "include_related_data": False,
}
req = urllib.request.Request(
    f"http://127.0.0.1:{http_port}/tools/run_orchestrated_investigation",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=30) as response:
    report = json.loads(response.read().decode("utf-8"))

assert report["target"] == f"node/{node_name}", report
trace = report.get("tool_path_trace") or {}
step_provenance = trace.get("step_provenance") or []
node_steps = [item for item in step_provenance if item.get("step_id") == "collect-target-evidence"]
assert node_steps, report
provenance = node_steps[0]["provenance"]
assert provenance["requested_capability"] == "node_evidence_plane", provenance
assert provenance["actual_route"]["source_kind"] == "peer_mcp", provenance
assert provenance["actual_route"]["mcp_server"] == "prometheus-mcp-server", provenance
assert provenance["route_satisfaction"] in {"preferred", "fallback"}, provenance
PY

echo "==> Local node validation passed"
