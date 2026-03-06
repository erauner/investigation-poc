#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_A="${CLUSTER_A:-investigation-a}"
CLUSTER_B="${CLUSTER_B:-investigation-b}"
CONTEXT_A="kind-${CLUSTER_A}"
CONTEXT_B="kind-${CLUSTER_B}"
SMOKE_NAMESPACE="${SMOKE_NAMESPACE:-kagent-smoke}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

wait_for_unhealthy_pod() {
  local context="$1"
  local namespace="$2"
  local attempts="${3:-36}"
  local sleep_seconds="${4:-5}"

  for _ in $(seq 1 "${attempts}"); do
    if kubectl --context "${context}" -n "${namespace}" get pods 2>/dev/null | grep -Eq 'CrashLoopBackOff|Error|Failed'; then
      return 0
    fi
    sleep "${sleep_seconds}"
  done

  echo "Timed out waiting for an unhealthy pod in ${context}/${namespace}" >&2
  kubectl --context "${context}" -n "${namespace}" get pods >&2 || true
  exit 1
}

ensure_cluster() {
  local name="$1"
  if kind get clusters | grep -qx "${name}"; then
    echo "kind cluster ${name} already exists"
  else
    kind create cluster --name "${name}" --wait 120s
  fi
}

cleanup() {
  KUBECTL_CONTEXT="${CONTEXT_A}" NS="${SMOKE_NAMESPACE}" "${ROOT_DIR}/scripts/smoke-workload.sh" delete >/dev/null 2>&1 || true
  KUBECTL_CONTEXT="${CONTEXT_B}" NS="${SMOKE_NAMESPACE}" "${ROOT_DIR}/scripts/smoke-workload.sh" delete >/dev/null 2>&1 || true
}
trap cleanup EXIT

need_cmd kind
need_cmd kubectl
need_cmd uv

tmp_dir="$(mktemp -d)"
kube_a="${tmp_dir}/kube-a"
kube_b="${tmp_dir}/kube-b"
merged_kubeconfig="${tmp_dir}/kubeconfig"
registry_path="${tmp_dir}/clusters.yaml"

ensure_cluster "${CLUSTER_A}"
ensure_cluster "${CLUSTER_B}"
kind export kubeconfig --name "${CLUSTER_A}" >/dev/null
kind export kubeconfig --name "${CLUSTER_B}" >/dev/null

kind get kubeconfig --name "${CLUSTER_A}" > "${kube_a}"
kind get kubeconfig --name "${CLUSTER_B}" > "${kube_b}"
KUBECONFIG="${kube_a}:${kube_b}" kubectl config view --flatten > "${merged_kubeconfig}"

cat > "${registry_path}" <<YAML
default_cluster: kind-a
clusters:
  kind-a:
    kube_context: ${CONTEXT_A}
    prometheus_url: http://localhost:9090
    default: true
    label_aliases: ["${CLUSTER_A}"]
  kind-b:
    kube_context: ${CONTEXT_B}
    prometheus_url: http://localhost:9090
    label_aliases: ["${CLUSTER_B}"]
YAML

echo "==> Applying differentiated smoke workloads"
KUBECTL_CONTEXT="${CONTEXT_A}" NS="${SMOKE_NAMESPACE}" FAILURE_EXIT_CODE=1 FAILURE_MESSAGE=cluster-a "${ROOT_DIR}/scripts/smoke-workload.sh" apply
KUBECTL_CONTEXT="${CONTEXT_B}" NS="${SMOKE_NAMESPACE}" FAILURE_EXIT_CODE=42 FAILURE_MESSAGE=cluster-b "${ROOT_DIR}/scripts/smoke-workload.sh" apply
wait_for_unhealthy_pod "${CONTEXT_A}" "${SMOKE_NAMESPACE}"
wait_for_unhealthy_pod "${CONTEXT_B}" "${SMOKE_NAMESPACE}"

echo "==> Validating backend cluster routing"
CLUSTER_REGISTRY_PATH="${registry_path}" \
KUBECONFIG_PATH="${merged_kubeconfig}" \
uv run python - <<'PY'
import json
from investigation_service.models import CollectContextRequest
from investigation_service.tools import collect_workload_context

def run(cluster: str) -> dict:
    context = collect_workload_context(
        CollectContextRequest(
            cluster=cluster,
            namespace="kagent-smoke",
            target="pod/crashy",
        )
    )
    return context.model_dump(mode="json")

a = run("kind-a")
b = run("kind-b")

print(json.dumps({"kind-a": a["cluster"], "kind-b": b["cluster"]}, indent=2))

if a["cluster"] != "kind-a":
    raise SystemExit("cluster alias mismatch for kind-a")
if b["cluster"] != "kind-b":
    raise SystemExit("cluster alias mismatch for kind-b")

a_text = "\n".join(a.get("evidence", []))
b_text = "\n".join(b.get("evidence", []))
if "cluster-a" not in a.get("log_excerpt", ""):
    raise SystemExit("kind-a log excerpt did not include cluster-a")
if "cluster-b" not in b.get("log_excerpt", ""):
    raise SystemExit("kind-b log excerpt did not include cluster-b")

containers_a = a.get("object_state", {}).get("containers", [])
containers_b = b.get("object_state", {}).get("containers", [])
if containers_a and containers_a[0].get("lastTerminationExitCode") != 1:
    raise SystemExit("kind-a container details did not include exit code=1")
if containers_b and containers_b[0].get("lastTerminationExitCode") != 42:
    raise SystemExit("kind-b container details did not include exit code=42")

print("multi-cluster host validation passed")
PY
