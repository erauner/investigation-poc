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
  `After resolving any vague target, prefer run_orchestrated_investigation as the default end-to-end runtime path.`
  `run_orchestrated_investigation keeps batch selection, external-step materialization, advancement, and final rendering in product code.`
  `Treat handoff_active_evidence_batch, get_active_evidence_batch, submit_evidence_step_artifacts, and advance_investigation_runtime as lower-level fine-grained runtime seams for debugging or explicit adapter choreography.`
  `Treat execute_investigation_step and update_investigation_plan as lower-level fallback/debug primitives.`
  `Use render_investigation_report only as a secondary low-level render seam when you are explicitly debugging the staged runtime path.`
  `Use exactly these Markdown headings verbatim: ## Diagnosis, ## Evidence, ## Related Data, ## Limitations, ## Recommended next step.`
- Do not use this command as the primary alert entrypoint. Use `/investigation-tools:investigate-alert` for alert triage.
- Return the agent's result directly.
- Treat this command as read-only investigation help. Do not take write actions, cleanup actions, or mutation requests through this command.
- If no arguments were supplied, ask the user what they want investigated.
