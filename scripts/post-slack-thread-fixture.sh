#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-alert}"

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi

FIXTURE_TOKEN="${SLACK_FIXTURE_TOKEN:-${SLACK_USER_TOKEN:-}}"
ALLOW_SELF_BOT_FIXTURE="${ALLOW_SELF_BOT_FIXTURE:-false}"

if [[ -z "${FIXTURE_TOKEN}" ]]; then
  echo "SLACK_FIXTURE_TOKEN or SLACK_USER_TOKEN is required" >&2
  exit 1
fi

if [[ "${FIXTURE_TOKEN}" == "${SLACK_BOT_TOKEN:-}" ]] && [[ "${ALLOW_SELF_BOT_FIXTURE}" != "true" ]]; then
  echo "Refusing to post fixture with the investigation bot token." >&2
  echo "Set SLACK_FIXTURE_TOKEN to a different identity, or ALLOW_SELF_BOT_FIXTURE=true to override." >&2
  exit 1
fi

CHANNELS="${SLACK_CHANNEL_IDS:-}"
if [[ -z "${CHANNELS}" ]]; then
  echo "SLACK_CHANNEL_IDS is required" >&2
  exit 1
fi

CHANNEL_ID="${SLACK_CHANNEL_ID:-${CHANNELS%%,*}}"
if [[ -z "${CHANNEL_ID}" ]]; then
  echo "Could not determine Slack channel ID" >&2
  exit 1
fi

case "${MODE}" in
  alert)
    MESSAGE="$(cat <<'EOF'
PodCrashLooping firing for pod/crashy in namespace kagent-smoke

Labels:
- alertname=PodCrashLooping
- namespace=kagent-smoke
- pod=crashy
- severity=warning

Annotations:
- summary=Pod crash loop detected
- description=Container crashy is restarting repeatedly

status: firing
startsAt: 2026-03-12T04:00:00Z
generatorURL: http://alertmanager.example.local
EOF
)"
    ;;
  generic)
    MESSAGE="$(cat <<'EOF'
We are seeing errors in the demo app and need a quick look.

The issue seems intermittent and there is no alert payload attached yet.
EOF
)"
    ;;
  *)
    echo "Unsupported fixture mode: ${MODE}" >&2
    echo "Usage: $0 [alert|generic]" >&2
    exit 1
    ;;
esac

PAYLOAD="$(jq -n \
  --arg channel "${CHANNEL_ID}" \
  --arg text "${MESSAGE}" \
  '{channel: $channel, text: $text}')"

RESPONSE="$(curl -sS https://slack.com/api/chat.postMessage \
  -H "Authorization: Bearer ${FIXTURE_TOKEN}" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "${PAYLOAD}")"

if [[ "$(jq -r '.ok' <<<"${RESPONSE}")" != "true" ]]; then
  echo "${RESPONSE}" | jq .
  exit 1
fi

echo "${RESPONSE}" | jq '{channel, ts, text: .message.text}'
