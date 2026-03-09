---
description: Investigate a Kubernetes workload, service, node, or operator target.
argument-hint: [task]
allowed-tools: mcp__kagent__invoke_agent
---

Use the `mcp__kagent__invoke_agent` tool.

- Set `agent` to `kagent/incident-triage`.
- Treat this command as the generic investigation path.
- Build `task` as this deterministic wrapper, then append the user's arguments verbatim under `Original user request:`.
- Wrapper header:
  `[INVESTIGATION_ENTRYPOINT]=generic`
  `Use the planner-led investigation flow.`
  `If the target is vague or operator-backed, resolve it first with resolve_primary_target.`
  `Then build_investigation_plan, seed execution_context from the built plan, and prefer advance_investigation_runtime for exactly one active evidence batch.`
  `If advance_investigation_runtime returns a next_active_batch that clearly asks for one more bounded follow-up evidence batch, advance it once more.`
  `Treat execute_investigation_step and update_investigation_plan as lower-level fallback/debug primitives rather than the preferred runtime-progress path.`
  `Use render_investigation_report late as the canonical final report tool for the five-section response.`
  `Treat Kubernetes MCP and Prometheus MCP as first-class evidence planes when they are the most direct bounded source of evidence.`
- Do not use this command as the primary alert entrypoint. Use `/investigate-alert` for alert triage.
- Return the agent's result directly.
- Treat this command as read-only investigation help. Do not take write actions, cleanup actions, or mutation requests through this command.
- If no arguments were supplied, ask the user what they want investigated.
