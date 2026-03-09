#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_NAMESPACE="${SMOKE_NAMESPACE:-operator-smoke}"
KEEP_SMOKE="${KEEP_SMOKE:-0}"
KEEP_CLUSTER="${KEEP_CLUSTER:-0}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-investigation}"
KIND_CONTEXT="${KIND_CONTEXT:-kind-${KIND_CLUSTER_NAME}}"
ALERT_PROMPT="${ALERT_PROMPT:-Investigate alert PodCrashLooping for pod crashy in namespace ${SMOKE_NAMESPACE}. Resolve the target if needed, build a plan, execute one bounded evidence batch, update the plan, and render the final investigation report late. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.}"
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
  kubectl --context "${KIND_CONTEXT}" -n kagent get agent/incident-triage >/dev/null 2>&1 || return 1
}

operator_stack_is_reusable() {
  kubectl --context "${KIND_CONTEXT}" -n homelab-operator-system \
    get deploy/homelab-operator-controller-manager >/dev/null 2>&1
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
  run_make kind-setup
}

ensure_operator_stack() {
  if operator_stack_is_reusable; then
    echo "==> Reusing installed homelab-operator"
    return 0
  fi

  echo "==> Installing homelab-operator into the local kind cluster"
  run_make kind-install-operator
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

wait_for_namespace_ready() {
  local namespace="$1"
  local attempts="${2:-36}"
  local sleep_seconds="${3:-5}"

  for _ in $(seq 1 "${attempts}"); do
    if ! kubectl get namespace "${namespace}" >/dev/null 2>&1; then
      return 0
    fi

    phase="$(kubectl get namespace "${namespace}" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    if [[ "${phase}" != "Terminating" ]]; then
      return 0
    fi

    sleep "${sleep_seconds}"
  done

  echo "Timed out waiting for namespace ${namespace} to leave Terminating state" >&2
  kubectl get namespace "${namespace}" -o yaml >&2 || true
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

cleanup() {
  if [[ "${KEEP_SMOKE}" != "1" ]]; then
    run_make operator-smoke-clean >/dev/null 2>&1 || true
  fi
  if [[ "${KEEP_CLUSTER}" == "0" && "${CLUSTER_PREEXISTED}" == "0" ]]; then
    run_make kind-down >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

need_cmd kind
need_cmd kubectl
need_cmd helm
need_cmd kagent
need_cmd docker
need_cmd go
need_cmd python3

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required" >&2
  exit 1
fi

ensure_kind_stack
ensure_operator_stack
wait_for_namespace_ready "${SMOKE_NAMESPACE}"

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

tmp_dir="$(mktemp -d)"
alert_raw="${tmp_dir}/alert.raw"
alert_output="${tmp_dir}/alert.md"

echo "==> Running planner-led alert prompt through the agent path"
run_make kagent-smoke-test TASK="${ALERT_PROMPT}" >"${alert_raw}"

python3 - "${alert_raw}" "${alert_output}" "${crashy_pod}" <<'PY'
import json
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
pod_name = sys.argv[3]
raw = src.read_text()
text = raw.strip()
if text.startswith("{"):
    payload = json.loads(raw)
    for artifact in payload.get("artifacts", []):
        for part in artifact.get("parts", []):
            if part.get("kind") == "text" and part.get("text"):
                dst.write_text(part["text"])
                break
        else:
            continue
        break
    else:
        dst.write_text(raw)
else:
    dst.write_text(raw)

body = dst.read_text()
print(body)
required = [
    "Diagnosis",
    "Evidence",
    "Related Data",
    "Limitations",
    "Recommended next step",
]
for heading in required:
    if heading not in body:
        raise AssertionError(f"missing heading: {heading}")
if "PodCrashLooping" not in body:
    raise AssertionError("expected alert context in final output")
if "pod/crashy" not in body:
    raise AssertionError("expected original alert-derived target in final output")
if pod_name not in body:
    raise AssertionError("expected resolved crashy pod context in final output")
if "BackOff" not in body and "CrashLoopBackOff" not in body and "crash loop" not in body.lower():
    raise AssertionError("expected crash-loop evidence in alert output")
PY

echo "==> Planner-led alert validation passed"
