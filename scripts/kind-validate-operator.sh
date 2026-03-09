#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_NAMESPACE="${SMOKE_NAMESPACE:-operator-smoke}"
KEEP_SMOKE="${KEEP_SMOKE:-0}"
KEEP_CLUSTER="${KEEP_CLUSTER:-0}"
WORKLOAD_PROMPT="${WORKLOAD_PROMPT:-Investigate the unhealthy pod in namespace ${SMOKE_NAMESPACE}. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.}"
DIRECT_TARGET_PROMPT="${DIRECT_TARGET_PROMPT:-Investigate Backend/crashy in namespace ${SMOKE_NAMESPACE}. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.}"
CANONICAL_REPORT_PROMPT="${CANONICAL_REPORT_PROMPT:-Use render_investigation_report as the canonical final report tool. Investigate the unhealthy pod in namespace ${SMOKE_NAMESPACE} and return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

run_make() {
  make -C "${ROOT_DIR}" "$@"
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
need_cmd kagent
need_cmd docker
need_cmd go

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required" >&2
  exit 1
fi

tmp_dir="$(mktemp -d)"
standard_output="${tmp_dir}/standard.md"
canonical_report_output="${tmp_dir}/canonical-report.md"
direct_target_output="${tmp_dir}/direct-target.md"
standard_raw="${tmp_dir}/standard.raw"
direct_target_raw="${tmp_dir}/direct-target.raw"
canonical_report_raw="${tmp_dir}/canonical-report.raw"

echo "==> Setting up local kind stack"
run_make kind-setup

echo "==> Installing homelab-operator into the local kind cluster"
run_make kind-install-operator

echo "==> Applying operator smoke workload"
run_make operator-smoke-apply
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

echo "==> Running direct operator target validation prompt"
run_make kagent-smoke-test TASK="${DIRECT_TARGET_PROMPT}" >"${direct_target_raw}"
normalize_agent_output "${direct_target_raw}" "${direct_target_output}"
cat "${direct_target_output}"

for heading in "Diagnosis" "Evidence" "Related Data" "Limitations" "Recommended next step"; do
  require_heading "${heading}" "${direct_target_output}"
done

if ! grep -Eiq 'Backend/crashy|operator-managed workload|homelab-operator' "${direct_target_output}"; then
  echo "Expected direct operator target output to retain operator target context" >&2
  exit 1
fi

echo "==> Running explicit canonical render prompt"
run_make kagent-smoke-test TASK="${CANONICAL_REPORT_PROMPT}" >"${canonical_report_raw}"
normalize_agent_output "${canonical_report_raw}" "${canonical_report_output}"
cat "${canonical_report_output}"

for heading in "Diagnosis" "Evidence" "Related Data" "Limitations" "Recommended next step"; do
  require_heading "${heading}" "${canonical_report_output}"
done

echo "==> Operator-backed local kind validation passed"
