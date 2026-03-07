#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-}"
FIXTURE_DIR="${FIXTURE_DIR:-${ROOT_DIR}/test-fixtures/operator-smoke}"
KUBECTL_CONTEXT="${KUBECTL_CONTEXT:-}"
NS="${NS:-operator-smoke}"

kubectl_cmd() {
  if [[ -n "${KUBECTL_CONTEXT}" ]]; then
    kubectl --context "${KUBECTL_CONTEXT}" "$@"
  else
    kubectl "$@"
  fi
}

usage() {
  cat <<USAGE
Usage: $0 <apply|delete>

Env vars:
  FIXTURE_DIR      Fixture directory (default: test-fixtures/operator-smoke)
  KUBECTL_CONTEXT  Optional kubectl context override
  NS               Namespace to remove on delete (default: operator-smoke)
USAGE
}

case "${ACTION}" in
  apply)
    kubectl_cmd apply -k "${FIXTURE_DIR}"
    ;;
  delete)
    kubectl_cmd delete namespace "${NS}" --ignore-not-found
    ;;
  *)
    usage
    exit 1
    ;;
esac
