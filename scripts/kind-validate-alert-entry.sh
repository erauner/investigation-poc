#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_NAMESPACE="${SMOKE_NAMESPACE:-operator-smoke}"
KEEP_SMOKE="${KEEP_SMOKE:-0}"
KEEP_CLUSTER="${KEEP_CLUSTER:-0}"
HTTP_PORT="${HTTP_PORT:-18080}"
HTTP_URL="http://127.0.0.1:${HTTP_PORT}"
ALERT_K8S_OVERLAY="${ALERT_K8S_OVERLAY:-k8s-overlays/local-kind-alert-http}"

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

wait_for_crashloop_evidence() {
  local namespace="$1"
  local pod_name="$2"
  local attempts="${3:-36}"
  local sleep_seconds="${4:-5}"

  for _ in $(seq 1 "${attempts}"); do
    if python3 - "${namespace}" "${pod_name}" <<'PY'
import json
import subprocess
import sys

namespace, pod_name = sys.argv[1], sys.argv[2]

pod = subprocess.run(
    ["kubectl", "-n", namespace, "get", "pod", pod_name, "-o", "json"],
    check=False,
    capture_output=True,
    text=True,
)
if pod.returncode != 0:
    sys.exit(1)

parsed = json.loads(pod.stdout)
container_statuses = parsed.get("status", {}).get("containerStatuses", []) or []
restart_count = sum(item.get("restartCount", 0) for item in container_statuses)
waiting_reasons = {
    item.get("state", {}).get("waiting", {}).get("reason")
    for item in container_statuses
    if item.get("state", {}).get("waiting", {}).get("reason")
}
last_reasons = {
    item.get("lastState", {}).get("terminated", {}).get("reason")
    for item in container_statuses
    if item.get("lastState", {}).get("terminated", {}).get("reason")
}

events = subprocess.run(
    [
        "kubectl",
        "-n",
        namespace,
        "get",
        "events",
        "--field-selector",
        f"involvedObject.kind=Pod,involvedObject.name={pod_name}",
        "--sort-by=.lastTimestamp",
        "-o",
        "custom-columns=REASON:.reason,MESSAGE:.message",
        "--no-headers",
    ],
    check=False,
    capture_output=True,
    text=True,
)
event_text = events.stdout.lower()

has_backoff_signal = (
    "crashloopbackoff" in event_text
    or "backoff" in event_text
    or "crashloopbackoff" in waiting_reasons
    or "error" in last_reasons
)

sys.exit(0 if restart_count > 0 and has_backoff_signal else 1)
PY
    then
      return 0
    fi
    sleep "${sleep_seconds}"
  done

  echo "Timed out waiting for crash-loop evidence for pod ${pod_name} in namespace ${namespace}" >&2
  kubectl -n "${namespace}" get pod "${pod_name}" -o yaml >&2 || true
  kubectl -n "${namespace}" get events --sort-by=.lastTimestamp >&2 || true
  exit 1
}

wait_for_http_ready() {
  local attempts="${1:-30}"
  local sleep_seconds="${2:-2}"

  for _ in $(seq 1 "${attempts}"); do
    if python3 - "${HTTP_URL}" <<'PY'
import sys
import urllib.request

url = f"{sys.argv[1]}/healthz"
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

  echo "Timed out waiting for investigation-service HTTP readiness" >&2
  exit 1
}

cleanup() {
  if [[ -n "${http_port_forward_pid:-}" ]]; then
    kill "${http_port_forward_pid}" >/dev/null 2>&1 || true
    wait "${http_port_forward_pid}" 2>/dev/null || true
  fi
  if [[ "${KEEP_SMOKE}" != "1" ]]; then
    run_make operator-smoke-clean >/dev/null 2>&1 || true
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
need_cmd go
need_cmd python3

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required" >&2
  exit 1
fi

echo "==> Setting up local kind stack"
run_make kind-up
K8S_OVERLAY="${ALERT_K8S_OVERLAY}" run_make kind-install-kagent

echo "==> Installing homelab-operator into the local kind cluster"
run_make kind-install-operator

echo "==> Applying operator smoke workload"
run_make operator-smoke-apply
wait_for_unhealthy_pod "${SMOKE_NAMESPACE}"
kubectl -n "${SMOKE_NAMESPACE}" get pods

crashy_pod="$(kubectl -n "${SMOKE_NAMESPACE}" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep '^crashy-' | head -n 1)"
if [[ -z "${crashy_pod}" ]]; then
  echo "Failed to identify crashy pod in namespace ${SMOKE_NAMESPACE}" >&2
  exit 1
fi

wait_for_crashloop_evidence "${SMOKE_NAMESPACE}" "${crashy_pod}"

echo "==> Port-forwarding investigation-service HTTP API"
kubectl -n kagent port-forward svc/investigation-service "${HTTP_PORT}:8080" >/tmp/kind-validate-alert-entry-http.log 2>&1 &
http_port_forward_pid=$!
wait_for_http_ready

echo "==> Calling explicit alert triage entrypoint"
python3 - "${HTTP_URL}" "${SMOKE_NAMESPACE}" "${crashy_pod}" <<'PY'
import json
import sys
import urllib.request

base_url, namespace, pod_name = sys.argv[1], sys.argv[2], sys.argv[3]
payload = {
    "alertname": "PodCrashLooping",
    "labels": {
        "namespace": namespace,
        "pod": pod_name,
    },
    "include_related_data": False,
}
data = json.dumps(payload).encode("utf-8")
request = urllib.request.Request(
    f"{base_url}/tools/build_alert_investigation_report",
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(request, timeout=30) as response:
    body = json.loads(response.read().decode("utf-8"))

print(json.dumps(body, indent=2))

assert body["target"] == f"pod/{pod_name}", body
assert body["diagnosis"] == "Crash Loop Detected", body
assert body["evidence"], body
assert any(note == "alertname=PodCrashLooping" for note in body["normalization_notes"]), body
assert body["related_data"] == [], body
assert body["related_data_note"] is None, body
assert any("BackOff" in item or "CrashLoopBackOff" in item for item in body["evidence"]), body
PY

echo "==> Alert entrypoint validation passed"
