# Claude Code Plugin Marketplace

This directory is a repo-local Claude Code plugin marketplace for testing the investigation workflow through slash commands.

## Included plugin

- `investigation-tools`

That plugin is intentionally thin:

- it defines a slash command for investigation
- it handles explicit alert-form input through the unified investigate command
- it provides plugin-scoped MCP wiring to the `kagent-controller` endpoint
- it keeps the actual investigation logic in the controller + agent + backend path
- it steers generic and explicit alert-form requests into the same planner-led resolve -> plan -> bounded execution -> update -> render-late model

## Breaking change

The separate alert-specific command was removed.

- old: `/investigation-tools:investigate-alert ...`
- new: `/investigation-tools:investigate Investigate alert ...`

## Local install flow

From the repo root:

```bash
./scripts/port-forward-controller-mcp.sh
```

In Claude Code:

```text
/plugin marketplace add ./claude-code-marketplace
/plugin install investigation-tools@investigation-poc-marketplace
```

After install, restart Claude Code and run:

```text
/investigation-tools:investigate Investigate the unhealthy pod in namespace kagent-smoke.
```

The same command should also handle explicit alert-form input:

```text
/investigation-tools:investigate Investigate alert PodCrashLooping for pod crashy-abc123 in namespace kagent-smoke.
```

## Architecture

This plugin follows the same controller-backed path as the Desktop extension:

`Claude Code plugin -> kagent-controller -> incident-triage -> investigation-mcp-server`

## Local plain-slash development path

For faster local iteration, this repo also includes a standalone project command at `.claude/commands/investigate.md`.

That gives you an un-namespaced local command:

```text
/investigate Investigate the unhealthy pod in namespace kagent-smoke.
```

Alert-shaped example:

```text
/investigate Investigate alert PodCrashLooping for pod crashy-abc123 in namespace kagent-smoke.
```

Use the standalone command for quick iteration, then use the plugin marketplace path for packaging and sharing.

An optional skill version also exists at `.claude/skills/investigation-helper/SKILL.md` if you want Claude to auto-discover the capability, but the command form is the primary manual test path.

## Local plugin-dir test path

Before using marketplace install, you can also load the plugin directly:

```bash
claude --plugin-dir ./claude-code-marketplace/investigation-tools
```

Then run:

```text
/investigation-tools:investigate Investigate the unhealthy pod in namespace kagent-smoke.
```
