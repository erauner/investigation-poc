# Investigation Interface Contract

This document defines the shared user-facing surface for the investigation product across:

- Claude Code local command
- Claude Code plugin
- Claude Desktop extension

The goal is to keep transport-specific packaging from changing the product semantics.

## Canonical action

The primary user-facing action is:

- `Investigate`

That action means:

- take a Kubernetes problem description or alert-shaped request
- investigate read-only
- route through the controller + agent + backend path
- return an investigation result for a human operator

## Read-only boundary

The `Investigate` surface is read-only.

It is for:

- investigation
- diagnosis
- explanation
- deployment/pod/service inspection
- alert follow-up

It is not for:

- delete
- patch
- restart
- rollout
- cleanup
- write actions of any kind

Any future mutation surface should use a separate action family with explicitly different names.

## Shared semantics

Across clients, `Investigate` should preserve these semantics:

- default path is `kagent-controller -> homelab-k8s-custom-agent -> investigation-mcp-server`
- output is investigation-oriented, not mutation-oriented
- cluster-awareness rules are backend-owned
- report composition remains backend-owned
- controller and agent implementation details stay hidden from the primary UX

## User-facing names

Use these names as the primary surface:

- Claude Code local: `/investigate`
- Claude Code plugin: `/investigation-tools:investigate`
- Claude Desktop tool: `investigate`

Do not lead with internal names like:

- `invoke_agent`
- `investigate_with_agent`
- agent refs

Those may exist for compatibility or debugging, but they are not the product surface.

## Optional discovery/debug surfaces

The following are acceptable as secondary/debug surfaces:

- `list_investigation_agents`
- optional skills for auto-discovery support

They should not replace `Investigate` as the primary user entrypoint.

## Shared config naming

Use one conceptual naming family across clients:

- `INVESTIGATION_REMOTE_MCP_URL`
- `INVESTIGATION_REMOTE_MCP_TOKEN`
- `INVESTIGATION_DEFAULT_AGENT_REF`

Desktop can map user settings into these names.
Claude Code plugin config can consume these names through environment expansion.

## Intentional client differences

These differences are expected and acceptable.

### Claude Code

- local project command for fast iteration
- plugin packaging for sharing
- optional skills or subagents for additive automation

### Claude Desktop

- `.mcpb` packaging
- local stdio runtime
- settings UI through the extension manifest

These transport differences should not change the meaning of `Investigate`.
