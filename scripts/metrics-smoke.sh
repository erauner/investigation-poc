#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
METRICS_SMOKE_SCENARIO="${METRICS_SMOKE_SCENARIO:-healthy_complete}"
FIXTURE_DIR="${FIXTURE_DIR:-${ROOT_DIR}/test-fixtures/metrics-smoke/overlays/${METRICS_SMOKE_SCENARIO}}"

usage() {
  echo "Usage: $0 <apply|delete>"
}

validate_scenario() {
  case "${METRICS_SMOKE_SCENARIO}" in
    healthy_complete|weak_but_usable|empty_or_broken|loki_complementary)
      ;;
    *)
      echo "Unsupported METRICS_SMOKE_SCENARIO: ${METRICS_SMOKE_SCENARIO}" >&2
      exit 1
      ;;
  esac
}

ensure_fixture_dir() {
  if [[ ! -d "${FIXTURE_DIR}" ]]; then
    echo "Fixture directory not found: ${FIXTURE_DIR}" >&2
    exit 1
  fi
}

case "${ACTION}" in
  apply)
    validate_scenario
    ensure_fixture_dir
    kubectl apply -k "${FIXTURE_DIR}"
    ;;
  delete)
    kubectl delete namespace metrics-smoke --ignore-not-found
    ;;
  *)
    usage
    exit 1
    ;;
esac
