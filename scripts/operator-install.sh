#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-investigation}"
KIND_CONTEXT="${KIND_CONTEXT:-kind-${KIND_CLUSTER_NAME}}"
HOMELAB_OPERATOR_DIR="${HOMELAB_OPERATOR_DIR:-${ROOT_DIR}/../homelab-operator}"
OPERATOR_IMAGE="${OPERATOR_IMAGE:-homelab-operator:local}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

need_path() {
  local path="$1"
  local label="$2"
  if [[ ! -e "${path}" ]]; then
    echo "${label} not found: ${path}" >&2
    exit 1
  fi
}

need_cmd kubectl
need_cmd kind
need_cmd docker
need_cmd go

if [[ "$(kubectl config current-context)" != "${KIND_CONTEXT}" ]]; then
  echo "Current context is '$(kubectl config current-context)'; expected '${KIND_CONTEXT}'" >&2
  echo "Run: make kind-up" >&2
  exit 1
fi

need_path "${HOMELAB_OPERATOR_DIR}" "homelab-operator repo"
need_path "${HOMELAB_OPERATOR_DIR}/config/crd/bases" "operator CRD directory"
need_path "${HOMELAB_OPERATOR_DIR}/test/e2e/manifests/kind/base" "operator kind manifests"

echo "==> Building operator image ${OPERATOR_IMAGE}"
docker build -t "${OPERATOR_IMAGE}" "${HOMELAB_OPERATOR_DIR}"

echo "==> Loading operator image into kind cluster ${KIND_CLUSTER_NAME}"
kind load docker-image "${OPERATOR_IMAGE}" --name "${KIND_CLUSTER_NAME}"

echo "==> Applying operator CRDs"
kubectl apply -f "${HOMELAB_OPERATOR_DIR}/config/crd/bases/"

for crd in clusters.homelab.erauner.dev frontends.homelab.erauner.dev backends.homelab.erauner.dev; do
  kubectl wait --for=condition=Established "crd/${crd}" --timeout=60s
done

echo "==> Deploying homelab-operator"
kubectl apply -k "${HOMELAB_OPERATOR_DIR}/test/e2e/manifests/kind/base"
kubectl -n homelab-operator-system rollout status deploy/homelab-operator-controller-manager --timeout=180s

echo "==> homelab-operator is ready"
