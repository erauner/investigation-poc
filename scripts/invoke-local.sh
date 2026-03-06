#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-kagent}"
AGENT_NAME="${AGENT_NAME:-investigation-agent}"
LOCAL_PORT="${LOCAL_PORT:-8083}"
TASK="${*:-List pods in namespace kagent-smoke and tell me which one is unhealthy.}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

need_cmd kubectl
need_cmd kagent

cleanup() {
  if [[ -n "${PF_PID:-}" ]] && kill -0 "${PF_PID}" >/dev/null 2>&1; then
    kill "${PF_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

kubectl -n "${NAMESPACE}" port-forward "svc/kagent-controller" "${LOCAL_PORT}:8083" >/tmp/kagent-pf.log 2>&1 &
PF_PID=$!
sleep 2

kagent --kagent-url "http://127.0.0.1:${LOCAL_PORT}" -n "${NAMESPACE}" invoke \
  --agent "${AGENT_NAME}" \
  --task "${TASK}"
