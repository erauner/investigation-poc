# Claude Desktop Extension

This directory contains a thin MCP Bundle client for Claude Desktop.

It does not reimplement the investigation logic. It runs a local Node MCP server over stdio and forwards high-level requests to the `kagent-controller` MCP endpoint, which then invokes the investigation agent behind the scenes.

## Which MCP server this uses

This extension is intentionally pointed at the controller MCP endpoint, not the raw investigation tool server.

- `kagent-controller` is the published, user-facing MCP surface. It exposes controller tools such as `list_agents` and `invoke_agent`.
- `investigation-mcp-server` is the lower-level MCP server that lives behind the custom agent. It exposes the investigation tools consumed by the agent inside the cluster.

The extension follows the product path:

`Claude Desktop -> local .mcpb proxy -> kagent-controller -> homelab-k8s-custom-agent -> investigation-mcp-server`

That is why `remote_mcp_url` should point at the controller route, for example `https://kagent-mcp.erauner.dev/mcp`.

## Why this exists

- `Claude Code` is already easiest through repo-local `.mcp.json` or a managed remote MCP config.
- `Claude Desktop` needs a local installable package, which is what `.mcpb` provides.
- The investigation agent remains the user-facing product surface.

## Tool surface

The extension intentionally exposes a higher-level, human-facing surface:

- `list_investigation_agents`
- `investigate_with_agent`

If you want more tools later, add them in [server/index.js](/Users/erauner/git/side/investigation-poc/desktop-extension/server/index.js) and [manifest.json](/Users/erauner/git/side/investigation-poc/desktop-extension/manifest.json).

## Build

From the repo root:

```bash
./scripts/build-desktop-extension.sh
```

That will:

1. install the Node dependencies into `desktop-extension/node_modules`
2. syntax-check the local proxy server
3. validate the MCPB manifest
4. pack a `.mcpb` file into `desktop-extension/dist/`

## Install in Claude Desktop

1. Build the bundle.
2. Open Claude Desktop.
3. Import the generated `.mcpb`.
4. Set `remote_mcp_url` to your reachable controller MCP endpoint.

Typical homelab value:

```text
https://kagent-mcp.erauner.dev/mcp
```

If your endpoint requires auth, set `remote_mcp_bearer_token`.

You can also set `default_agent_ref`. For this repo the expected value is:

```text
kagent/homelab-k8s-custom-agent
```

## Release

To create the standard bundle plus a versioned release copy with SHA256 output:

```bash
./scripts/release-desktop-extension.sh
```

Admin handoff notes live in [ADMIN_HANDOFF.md](/Users/erauner/git/side/investigation-poc/desktop-extension/ADMIN_HANDOFF.md).

## Notes

- `allow_insecure_tls` exists only for lab-grade self-signed endpoints.
- The extension is for Claude Desktop. Claude Code should keep using `.mcp.json` or a managed remote MCP server.
