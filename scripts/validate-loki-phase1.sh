#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT_DIR}"
echo "Running deterministic Loki Phase 1 regression suite (not kind E2E)"
if command -v uv >/dev/null 2>&1; then
  uv run pytest -q \
    tests/test_cluster_registry.py \
    tests/test_mcp_clients.py \
    tests/test_service_quality.py \
    tests/test_evidence_runner.py
else
  python3 -m pytest -q \
    tests/test_cluster_registry.py \
    tests/test_mcp_clients.py \
    tests/test_service_quality.py \
    tests/test_evidence_runner.py
fi
