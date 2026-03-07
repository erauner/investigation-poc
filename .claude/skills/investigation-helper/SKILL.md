---
name: investigation-helper
description: >
  Help Claude continue a Kubernetes investigation after an initial read-only investigation result already exists.
  Use when the user asks for interpretation, follow-up explanation, or a narrower next read-only diagnostic step for an already identified issue.
argument-hint: [task]
allowed-tools: mcp__kagent__invoke_agent
---

## Instructions

Use the `mcp__kagent__invoke_agent` tool.

- Set `agent` to `kagent/homelab-k8s-custom-agent`.
- Set `task` to the user's arguments exactly.
- Return the agent's result directly.
- Treat this skill as read-only investigation help for follow-up clarification. Do not take write actions, cleanup actions, or mutation requests through this skill.
- If no arguments were supplied, ask the user what they want investigated.
