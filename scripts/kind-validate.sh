#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_NAMESPACE="${SMOKE_NAMESPACE:-kagent-smoke}"
KEEP_SMOKE="${KEEP_SMOKE:-0}"
KEEP_CLUSTER="${KEEP_CLUSTER:-0}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-investigation}"
KIND_CONTEXT="${KIND_CONTEXT:-kind-${KIND_CLUSTER_NAME}}"
WORKLOAD_PROMPT="${WORKLOAD_PROMPT:-Investigate the unhealthy pod in namespace ${SMOKE_NAMESPACE}. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.}"
PLANNER_LED_PROMPT="${PLANNER_LED_PROMPT:-Resolve the target if needed, build a plan, execute one bounded evidence batch, update the plan, and render the final investigation report late. Investigate the unhealthy pod in namespace ${SMOKE_NAMESPACE} and return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.}"
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
  kubectl --context "${KIND_CONTEXT}" -n kagent get agent/investigation-agent >/dev/null 2>&1 || return 1
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

normalize_agent_output() {
  local src="$1"
  local dst="$2"
  python3 - "$src" "$dst" <<'PY'
import json
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
raw = src.read_text()
text = raw.strip()
if not text.startswith("{"):
    dst.write_text(raw)
    raise SystemExit(0)

payload = json.loads(raw)
for artifact in payload.get("artifacts", []):
    for part in artifact.get("parts", []):
        if part.get("kind") == "text" and part.get("text"):
            dst.write_text(part["text"])
            raise SystemExit(0)

dst.write_text(raw)
PY
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

extract_section() {
  local heading="$1"
  local file="$2"
  awk -v heading="${heading}" '
    function lower_trim(value) {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      return tolower(value)
    }
    $0 ~ /^###+ / || $0 ~ /^## / || $0 ~ /^[1-5]\. \*\*.*\*\*/ {
      if (in_section) {
        exit
      }
      line=$0
      sub(/^###+ /, "", line)
      sub(/^## /, "", line)
      sub(/^[0-9]+\. /, "", line)
      sub(/^\*\*/, "", line)
      sub(/\*\*[[:space:]]*$/, "", line)
      sub(/[[:space:]]*$/, "", line)
      in_section = (lower_trim(line) == lower_trim(heading))
      next
    }
    in_section {
      print
    }
  ' "${file}"
}

require_heading() {
  local heading="$1"
  local file="$2"
  if ! awk -v heading="${heading}" '
    function lower_trim(value) {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      return tolower(value)
    }
    $0 ~ /^###+ / || $0 ~ /^## / || $0 ~ /^[1-5]\. \*\*.*\*\*/ {
      line=$0
      sub(/^###+ /, "", line)
      sub(/^## /, "", line)
      sub(/^[0-9]+\. /, "", line)
      sub(/^\*\*/, "", line)
      sub(/\*\*[[:space:]]*$/, "", line)
      sub(/[[:space:]]*$/, "", line)
      if (lower_trim(line) == lower_trim(heading)) {
        found=1
      }
    }
    END { exit found ? 0 : 1 }
  ' "${file}"; then
    echo "Missing required heading: ${heading}" >&2
    exit 1
  fi
}

cleanup() {
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
need_cmd kagent

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required" >&2
  exit 1
fi

tmp_dir="$(mktemp -d)"
standard_output="${tmp_dir}/standard.md"
planner_led_output="${tmp_dir}/planner-led.md"
standard_raw="${tmp_dir}/standard.raw"
planner_led_raw="${tmp_dir}/planner-led.raw"

ensure_kind_stack
wait_for_namespace_ready "${SMOKE_NAMESPACE}"

echo "==> Applying smoke workload"
run_make kagent-smoke-apply
kubectl -n "${SMOKE_NAMESPACE}" get pods
wait_for_unhealthy_pod "${SMOKE_NAMESPACE}"
kubectl -n "${SMOKE_NAMESPACE}" get pods

echo "==> Running standard workload validation prompt"
run_make kagent-smoke-test TASK="${WORKLOAD_PROMPT}" >"${standard_raw}"
normalize_agent_output "${standard_raw}" "${standard_output}"
cat "${standard_output}"

for heading in "Diagnosis" "Evidence" "Related Data" "Limitations" "Recommended next step"; do
  require_heading "${heading}" "${standard_output}"
done

related_data_section="$(extract_section "Related Data" "${standard_output}")"
limitations_section="$(extract_section "Limitations" "${standard_output}")"

if [[ -z "${related_data_section}" ]]; then
  echo "Related Data section is empty" >&2
  exit 1
fi

if grep -Eiq "correlated changes|related data available" <<<"${limitations_section}"; then
  echo "Limitations still include correlated-change leakage" >&2
  echo "--- Limitations ---" >&2
  echo "${limitations_section}" >&2
  exit 1
fi

echo "==> Running explicit planner-led prompt"
run_make kagent-smoke-test TASK="${PLANNER_LED_PROMPT}" >"${planner_led_raw}"
normalize_agent_output "${planner_led_raw}" "${planner_led_output}"
cat "${planner_led_output}"

for heading in "Diagnosis" "Evidence" "Related Data" "Limitations" "Recommended next step"; do
  require_heading "${heading}" "${planner_led_output}"
done

echo "==> Local kind validation passed"
