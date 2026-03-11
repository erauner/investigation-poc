#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT_DIR}"
uv run pytest -q \
  tests/test_cluster_registry.py \
  tests/test_mcp_clients.py \
  tests/test_service_quality.py \
  tests/test_evidence_runner.py
