# Desktop Extension Admin Handoff

This package is the Claude Desktop distribution path for the homelab investigation MCP tools.

## Artifact

Build the bundle with:

```bash
./scripts/release-desktop-extension.sh
```

Primary output:

- `desktop-extension/dist/homelab-investigation-remote.mcpb`

Versioned release copy:

- `desktop-extension/dist/releases/homelab-investigation-remote-<version>.mcpb`

## What it does

- Runs a local Node MCP server in Claude Desktop.
- Proxies a high-level investigation surface to the `kagent-controller` MCP endpoint.
- The controller invokes the investigation agent behind the scenes.

## Endpoint distinction

This package should be configured against the published controller MCP route, not the raw `investigation-mcp-server` service.

- Deploy/publish for Desktop users: `kagent-controller` MCP endpoint
- Backend tool server used behind the scenes by the agent: `investigation-mcp-server`

Expected call path:

`Claude Desktop -> local extension -> kagent-controller -> homelab-k8s-custom-agent -> investigation-mcp-server`

## Required user configuration

- `remote_mcp_url`
  Example: `https://kagent-mcp.erauner.dev/mcp`

- `default_agent_ref`
  Example: `kagent/homelab-k8s-custom-agent`

## Optional user configuration

- `remote_mcp_bearer_token`
  Use if the remote MCP endpoint is behind a bearer-token boundary.

- `allow_insecure_tls`
  Only for lab endpoints with self-signed certificates.

## Exposed tools

- `investigate`
- `list_investigation_agents`

`investigate` is the primary user-facing action. `list_investigation_agents` is a secondary/debug surface.

`investigate` now applies deterministic entrypoint routing before it calls the controller-backed agent:

- generic requests steer toward `build_investigation_report`
- explicit alert-shaped requests steer toward `build_alert_investigation_report`

Alert routing is only selected when the request includes an explicit alert form such as `Investigate alert PodCrashLooping ...`, or when the caller passes structured `mode=alert` plus `alertname`. The raw `alertname=...` text forms remain available as a secondary debug fallback.

## Recommended rollout notes

- Desktop only: this is for Claude Desktop, not Claude Code.
- Claude Code should use repo-local `.mcp.json`, managed MCP config, or a directly configured remote HTTP MCP server.
- If you need org-controlled rollout, distribute the `.mcpb` through the Claude Desktop extension management flow documented by Anthropic.

## Validation before handoff

Run:

```bash
./scripts/build-desktop-extension.sh
```

Expected result:

- manifest validation passes
- bundle packs successfully
- output lands in `desktop-extension/dist/`
