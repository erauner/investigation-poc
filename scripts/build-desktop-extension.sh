#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT_DIR="${ROOT_DIR}/desktop-extension"
OUT_DIR="${EXT_DIR}/dist"
CACHE_DIR="${NPM_CONFIG_CACHE:-${ROOT_DIR}/.npm-cache}"
OUTPUT_FILE="${OUT_DIR}/investigation-remote.mcpb"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

need_cmd node
need_cmd npm
mkdir -p "${OUT_DIR}" "${CACHE_DIR}"

echo "==> Installing desktop extension dependencies"
env npm_config_cache="${CACHE_DIR}" npm --prefix "${EXT_DIR}" install --omit=dev

echo "==> Syntax-checking desktop extension server"
node --check "${EXT_DIR}/server/index.js"

echo "==> Validating MCP bundle manifest"
env npm_config_cache="${CACHE_DIR}" npx @anthropic-ai/mcpb validate "${EXT_DIR}/manifest.json"

echo "==> Packing MCP bundle"
rm -f "${OUTPUT_FILE}"
env npm_config_cache="${CACHE_DIR}" npx @anthropic-ai/mcpb pack "${EXT_DIR}" "${OUTPUT_FILE}"

echo "Built ${OUTPUT_FILE}"
