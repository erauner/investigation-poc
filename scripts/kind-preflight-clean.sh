#!/usr/bin/env bash
set -euo pipefail

KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-investigation}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

should_delete_cluster() {
  local cluster="$1"
  [[ "${cluster}" == "${KIND_CLUSTER_NAME}" ]] && return 0
  [[ "${cluster}" == "${KIND_CLUSTER_NAME}"-* ]] && return 0
  [[ "${cluster}" == "homelab-operator-e2e" ]] && return 0
  return 1
}

need_cmd kind

clusters="$(kind get clusters || true)"
if [[ -z "${clusters}" ]]; then
  echo "No kind clusters found."
  exit 0
fi

deleted=0
while IFS= read -r cluster; do
  [[ -z "${cluster}" ]] && continue
  if ! should_delete_cluster "${cluster}"; then
    continue
  fi
  echo "Deleting stale kind cluster: ${cluster}"
  kind delete cluster --name "${cluster}"
  deleted=1
done <<< "${clusters}"

if [[ "${deleted}" -eq 0 ]]; then
  echo "No stale kind clusters matched cleanup scope."
fi
