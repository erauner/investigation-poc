#!/usr/bin/env bash
set -euo pipefail

AGENT_NAME="${AGENT_NAME:-incident-triage-shadow}"
export AGENT_NAME

exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/invoke-local.sh" "$@"
