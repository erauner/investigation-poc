#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_NAMESPACE="${SMOKE_NAMESPACE:-kagent-smoke}"
KEEP_SMOKE="${KEEP_SMOKE:-0}"
KEEP_CLUSTER="${KEEP_CLUSTER:-0}"
WORKLOAD_PROMPT="${WORKLOAD_PROMPT:-Investigate the unhealthy pod in namespace ${SMOKE_NAMESPACE}. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.}"
TOP_LEVEL_PROMPT="${TOP_LEVEL_PROMPT:-Use build_investigation_report for the investigation. Investigate the unhealthy pod in namespace ${SMOKE_NAMESPACE} and return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

run_make() {
  make -C "${ROOT_DIR}" "$@"
}

extract_section() {
  local heading="$1"
  local file="$2"
  awk -v heading="### ${heading}" '
    $0 ~ /^### / {
      if (in_section) {
        exit
      }
      in_section = ($0 == heading)
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
  if ! grep -q "^### ${heading}$" "${file}"; then
    echo "Missing required heading: ${heading}" >&2
    exit 1
  fi
}

cleanup() {
  if [[ "${KEEP_SMOKE}" != "1" ]]; then
    run_make kagent-smoke-clean >/dev/null 2>&1 || true
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

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required" >&2
  exit 1
fi

tmp_dir="$(mktemp -d)"
standard_output="${tmp_dir}/standard.md"
top_level_output="${tmp_dir}/top-level.md"

echo "==> Setting up local kind stack"
run_make kind-setup

echo "==> Applying smoke workload"
run_make kagent-smoke-apply
kubectl -n "${SMOKE_NAMESPACE}" get pods

echo "==> Running standard workload validation prompt"
run_make kagent-smoke-test TASK="${WORKLOAD_PROMPT}" >"${standard_output}"
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

echo "==> Running explicit top-level report prompt"
run_make kagent-smoke-test TASK="${TOP_LEVEL_PROMPT}" >"${top_level_output}"
cat "${top_level_output}"

for heading in "Diagnosis" "Evidence" "Related Data" "Limitations" "Recommended next step"; do
  require_heading "${heading}" "${top_level_output}"
done

echo "==> Local kind validation passed"
