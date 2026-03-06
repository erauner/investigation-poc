#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${ROOT_DIR}/desktop-extension/dist"
RELEASE_DIR="${DIST_DIR}/releases"
MANIFEST_PATH="${ROOT_DIR}/desktop-extension/manifest.json"
BASE_BUNDLE="${DIST_DIR}/homelab-investigation-remote.mcpb"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

need_cmd jq
need_cmd shasum

"${ROOT_DIR}/scripts/build-desktop-extension.sh"

VERSION="$(jq -r '.version' "${MANIFEST_PATH}")"
RELEASE_BUNDLE="${RELEASE_DIR}/homelab-investigation-remote-${VERSION}.mcpb"

mkdir -p "${RELEASE_DIR}"
cp "${BASE_BUNDLE}" "${RELEASE_BUNDLE}"

echo "==> SHA256"
shasum -a 256 "${BASE_BUNDLE}" "${RELEASE_BUNDLE}"
