---
description: Investigate a Kubernetes issue through the homelab investigation agent.
argument-hint: [task]
allowed-tools: mcp__kagent__invoke_agent
---

Use the `mcp__kagent__invoke_agent` tool.

- Set `agent` to `kagent/homelab-k8s-custom-agent`.
- Set `task` to the user's arguments exactly.
- Return the agent's result directly.
- If no arguments were supplied, ask the user what they want investigated.
