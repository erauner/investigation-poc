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
  `Then build_investigation_plan and prefer handoff_active_evidence_batch as the default runtime-progress helper.`
  `Seed execution_context from the built plan, call handoff_active_evidence_batch with incident=<same build request> and execution_context=<seeded or returned execution_context>, and use the returned active_batch when external evidence still needs to be gathered.`
  `If handoff_active_evidence_batch returns an active_batch with externally preferred steps, satisfy those bounded steps with peer evidence-plane tools and call handoff_active_evidence_batch again with submitted_steps=<typed artifacts for the pending external steps>.`
  `If handoff_active_evidence_batch returns another active_batch that clearly asks for one more bounded follow-up evidence batch, hand it off once more.`
  `Treat get_active_evidence_batch, submit_evidence_step_artifacts, and advance_investigation_runtime as lower-level fine-grained runtime seams, and treat execute_investigation_step and update_investigation_plan as lower-level fallback/debug primitives.`
  `Use render_investigation_report late as the canonical final report tool for the five-section response.`
  `Use exactly these Markdown headings verbatim: ## Diagnosis, ## Evidence, ## Related Data, ## Limitations, ## Recommended next step.`
  `Treat Kubernetes MCP and Prometheus MCP as first-class evidence planes when they are the most direct bounded source of evidence.`
- Do not use this command as the primary alert entrypoint. Use `/investigate-alert` for alert triage.
- Return the agent's result directly.
- Treat this command as read-only investigation help. Do not take write actions, cleanup actions, or mutation requests through this command.
- If no arguments were supplied, ask the user what they want investigated.
