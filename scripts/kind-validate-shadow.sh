#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_NAMESPACE="${SMOKE_NAMESPACE:-kagent-smoke}"
KEEP_SMOKE="${KEEP_SMOKE:-0}"
KEEP_CLUSTER="${KEEP_CLUSTER:-0}"
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-investigation}"
KIND_CONTEXT="${KIND_CONTEXT:-kind-${KIND_CLUSTER_NAME}}"
WORKLOAD_PROMPT="${WORKLOAD_PROMPT:-Investigate the unhealthy pod in namespace ${SMOKE_NAMESPACE}. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.}"
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

require_heading() {
  local heading="$1"
  local file="$2"
  grep -Eiq "^## ${heading}$" "${file}" || {
    echo "Missing required heading: ${heading}" >&2
    exit 1
  }
}

extract_section() {
  local heading="$1"
  local file="$2"
  awk -v heading="${heading}" '
    $0 == "## " heading { in_section = 1; next }
    /^## / && in_section { exit }
    in_section { print }
  ' "${file}"
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
raw_output="${tmp_dir}/shadow.raw"
rendered_output="${tmp_dir}/shadow.md"

ensure_kind_stack
run_make kind-install-kagent-shadow
run_make kagent-smoke-apply

run_make kagent-shadow-test TASK="${WORKLOAD_PROMPT}" >"${raw_output}"
normalize_agent_output "${raw_output}" "${rendered_output}"
cat "${rendered_output}"

for heading in "Diagnosis" "Evidence" "Related Data" "Limitations" "Recommended next step"; do
  require_heading "${heading}" "${rendered_output}"
done

evidence_section="$(extract_section "Evidence" "${rendered_output}")"

if grep -Fq "InvolvedObject:" <<<"${evidence_section}"; then
  echo "Shadow Evidence leaked raw YAML content" >&2
  echo "--- Evidence ---" >&2
  echo "${evidence_section}" >&2
  exit 1
fi

if grep -Fq "events: Crash Loop Detected - Events indicate BackOff/CrashLoopBackOff behavior" <<<"${evidence_section}" && \
   grep -Fq "recent events: Back-off restarting failed container" <<<"${evidence_section}"; then
  echo "Shadow Evidence still contains duplicated crash-loop bullets" >&2
  echo "--- Evidence ---" >&2
  echo "${evidence_section}" >&2
  exit 1
fi

echo "==> Shadow kind validation passed"
