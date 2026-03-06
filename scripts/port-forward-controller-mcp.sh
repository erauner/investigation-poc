#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-kagent}"
SERVICE="${SERVICE:-kagent-controller}"
LOCAL_PORT="${LOCAL_PORT:-8083}"
REMOTE_PORT="${REMOTE_PORT:-8083}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

need_cmd kubectl

echo "Port-forwarding svc/${SERVICE} in namespace ${NAMESPACE} to http://127.0.0.1:${LOCAL_PORT}/mcp"
exec kubectl -n "${NAMESPACE}" port-forward "svc/${SERVICE}" "${LOCAL_PORT}:${REMOTE_PORT}"
