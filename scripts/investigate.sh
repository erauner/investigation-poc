#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${1:-kagent}"
shift || true
TASK="${*:-Investigate the unhealthy pod in namespace kagent-smoke. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.}"

NAMESPACE="${NAMESPACE}" ./scripts/invoke-local.sh "${TASK}"
