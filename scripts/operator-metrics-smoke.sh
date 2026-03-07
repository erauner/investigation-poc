#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_DIR="${FIXTURE_DIR:-${ROOT_DIR}/test-fixtures/operator-metrics-smoke}"
ACTION="${1:-apply}"
NS="${NS:-operator-metrics-smoke}"

usage() {
  cat <<EOF
Usage: $0 [apply|delete]

Environment:
  FIXTURE_DIR      Fixture directory (default: test-fixtures/operator-metrics-smoke)
  NS               Namespace to remove on delete (default: operator-metrics-smoke)
EOF
}

case "${ACTION}" in
  apply)
    kubectl apply -k "${FIXTURE_DIR}"
    ;;
  delete)
    kubectl delete namespace "${NS}" --ignore-not-found --wait=false
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
