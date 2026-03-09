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
  `Call handoff_active_evidence_batch first with incident=<same build request> and no handoff_token.`
  `If handoff_active_evidence_batch returns next_action=submit_external_steps, use required_external_step_ids to select the matching steps from active_batch.steps.`
  `For each required external step, build one submitted_steps item with step_id=<the contract step id>, actual_route=<the peer MCP server/tool actually used>, and the payload field named by artifact_type from that same step contract.`
  `On the follow-up handoff call, send incident=<same build request>, handoff_token=<returned handoff_token>, and submitted_steps=<the non-empty typed artifacts built from the required external steps>.`
  `Do not call handoff_active_evidence_batch again with an empty submitted_steps list after next_action=submit_external_steps.`
  `If handoff_active_evidence_batch returns next_action=call_handoff_again, call it once more with handoff_token=<returned handoff_token>.`
  `Treat get_active_evidence_batch, submit_evidence_step_artifacts, and advance_investigation_runtime as lower-level fine-grained runtime seams, and treat execute_investigation_step and update_investigation_plan as lower-level fallback/debug primitives.`
  `Use render_investigation_report late as the canonical final report tool for the five-section response.`
  `Use exactly these Markdown headings verbatim: ## Diagnosis, ## Evidence, ## Related Data, ## Limitations, ## Recommended next step.`
- Do not use this command as the primary alert entrypoint. Use `/investigation-tools:investigate-alert` for alert triage.
- Return the agent's result directly.
- Treat this command as read-only investigation help. Do not take write actions, cleanup actions, or mutation requests through this command.
- If no arguments were supplied, ask the user what they want investigated.
