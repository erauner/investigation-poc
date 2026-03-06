#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${1:-default}"
TARGET="${2:-pod/example}"
URL="${INVESTIGATE_URL:-http://localhost:8080}"

curl -s "${URL}/investigate" \
  -H 'content-type: application/json' \
  -d "{\"namespace\":\"${NAMESPACE}\",\"target\":\"${TARGET}\"}"
