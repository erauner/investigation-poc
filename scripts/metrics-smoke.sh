#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
FIXTURE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/test-fixtures/metrics-smoke"

usage() {
  echo "Usage: $0 <apply|delete>"
}

case "${ACTION}" in
  apply)
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
