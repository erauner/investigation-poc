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
  `If the request only says the unhealthy pod in a namespace, use Kubernetes MCP to identify the concrete unhealthy pod first, then continue with the planner-led control-plane path using that target.`
  `Then build_investigation_plan, call get_active_evidence_batch, satisfy externally preferred evidence steps with peer evidence-plane tools, and submit them with submit_evidence_step_artifacts before advancing the batch.`
  `Seed execution_context from the built plan, then prefer advance_investigation_runtime only after the external-preferred steps for that active batch have been submitted or when the batch is planner-owned only.`
  `Call advance_investigation_runtime with incident=<same build request> and execution_context=<seeded or returned execution_context>; do not call it with only batch_id.`
  `If advance_investigation_runtime returns a next_active_batch that clearly asks for one more bounded follow-up evidence batch, advance it once more.`
  `Treat execute_investigation_step and update_investigation_plan as lower-level fallback/debug primitives rather than the preferred runtime-progress path.`
  `Use render_investigation_report late as the canonical final report tool for the five-section response.`
  `Treat Kubernetes MCP and Prometheus MCP as first-class evidence planes when they are the most direct bounded source of evidence.`
- Do not use this command as the primary alert entrypoint. Use `/investigate-alert` for alert triage.
- Return the agent's result directly.
- Treat this command as read-only investigation help. Do not take write actions, cleanup actions, or mutation requests through this command.
- If no arguments were supplied, ask the user what they want investigated.
