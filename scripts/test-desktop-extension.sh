#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export INVESTIGATION_REMOTE_MCP_URL="${INVESTIGATION_REMOTE_MCP_URL:-https://kagent-mcp.erauner.dev/mcp}"
export INVESTIGATION_DEFAULT_AGENT_REF="${INVESTIGATION_DEFAULT_AGENT_REF:-kagent/homelab-k8s-custom-agent}"
export TASK="${TASK:-Investigate the unhealthy pod in namespace kagent-smoke.}"
export INVESTIGATION_REMOTE_MCP_TOKEN="${INVESTIGATION_REMOTE_MCP_TOKEN:-}"
export ALLOW_INSECURE_TLS="${ALLOW_INSECURE_TLS:-false}"
export ROOT_DIR

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

need_cmd node

HARNESS_DIR="${ROOT_DIR}/desktop-extension-reference"

if [ ! -d "${HARNESS_DIR}/node_modules" ]; then
  echo "Missing harness dependencies in ${HARNESS_DIR}/node_modules" >&2
  echo "Run: ./scripts/build-reference-desktop-extension.sh" >&2
  exit 1
fi

cd "${HARNESS_DIR}"

node --input-type=module - <<'JS'
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

const remoteUrl = process.env.INVESTIGATION_REMOTE_MCP_URL;
const defaultAgentRef = process.env.INVESTIGATION_DEFAULT_AGENT_REF;
const task = process.env.TASK;
const bearerToken = process.env.INVESTIGATION_REMOTE_MCP_TOKEN || '';
const allowInsecureTls = process.env.ALLOW_INSECURE_TLS || 'false';

const transport = new StdioClientTransport({
  command: 'node',
  args: [`${process.env.ROOT_DIR}/desktop-extension/server/index.js`],
  cwd: process.env.ROOT_DIR,
  env: {
    ...process.env,
    INVESTIGATION_REMOTE_MCP_URL: remoteUrl,
    INVESTIGATION_DEFAULT_AGENT_REF: defaultAgentRef,
    INVESTIGATION_REMOTE_MCP_TOKEN: bearerToken,
    ALLOW_INSECURE_TLS: allowInsecureTls
  }
});

const client = new Client({ name: 'desktop-extension-test', version: '0.1.0' });

try {
  await client.connect(transport);

  const tools = await client.listTools();
  console.log('==> Extension tools');
  console.log(JSON.stringify(tools, null, 2));

  const agents = await client.callTool({
    name: 'list_investigation_agents',
    arguments: {}
  });
  console.log('==> list_investigation_agents');
  console.log(JSON.stringify(agents.structuredContent ?? agents, null, 2));

  const result = await client.callTool({
    name: 'investigate',
    arguments: { task }
  });
  console.log('==> investigate');
  console.log(JSON.stringify(result.structuredContent ?? result, null, 2));
} finally {
  await transport.close();
}
JS
