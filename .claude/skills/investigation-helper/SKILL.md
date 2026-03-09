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

- Set `agent` to `kagent/incident-triage`.
- Build `task` as a deterministic entrypoint wrapper, then append the user's arguments verbatim under `Original user request:`.
- If the user supplied an explicit alert phrase such as `Investigate alert PodCrashLooping ...`, set the wrapper header to:
  `[INVESTIGATION_ENTRYPOINT]=alert`
  `Use the planner-led investigation flow for alert handling.`
  `After extracting alert facts, build_investigation_plan, call get_active_evidence_batch, satisfy externally preferred evidence steps with peer evidence-plane tools, and submit them with submit_evidence_step_artifacts before advancing the batch.`
  `Seed execution_context from the built plan, then prefer advance_investigation_runtime only after the external-preferred steps for that active batch have been submitted or when the batch is planner-owned only.`
  `Call advance_investigation_runtime with incident=<same build request> and execution_context=<seeded or returned execution_context>; do not call it with only batch_id.`
  `If advance_investigation_runtime returns a next_active_batch that clearly asks for one more bounded follow-up evidence batch, advance it once more.`
  `Treat execute_investigation_step and update_investigation_plan as lower-level fallback/debug primitives rather than the preferred runtime-progress path.`
  `Use render_investigation_report late as the canonical final report tool.`
  `Treat only identity fields such as namespace, pod, service, deployment, node, and container as workload identity.`
  `Treat source or monitoring fields such as prometheus, alertmanager, rule_group, generatorURL, datasource, and runbook_url as metadata, not as workload identity.`
  `Never derive a workload namespace from source or monitoring metadata.`
  `If live runtime evidence disagrees with the alert payload, call out the mismatch explicitly as possible stale alert metadata or drift between alert time and current state.`
  `Preserve the original alert name and the resolved operational target name explicitly in the final five-section answer when they are present in the request or report evidence.`
  `Return exactly these five sections and no extra appendix sections: Diagnosis, Evidence, Related Data, Limitations, Recommended next step.`
- As a secondary debug-only fallback, also accept `alertname=PodCrashLooping` or `alertname: PodCrashLooping`.
- Only treat the request as alert-shaped when one of those explicit alert forms is present.
- Do not treat `Backend/<name>`, `Frontend/<name>`, or `Cluster/<name>` as alert names.
- Otherwise set the wrapper header to:
  `[INVESTIGATION_ENTRYPOINT]=generic`
  `Use the planner-led investigation flow.`
  `If the target is vague or operator-backed, resolve it first with resolve_primary_target.`
  `If the request only says the unhealthy pod in a namespace, use Kubernetes MCP to identify the concrete unhealthy pod first, then continue with the planner-led control-plane path using that target.`
  `Then build_investigation_plan, call get_active_evidence_batch, satisfy externally preferred evidence steps with peer evidence-plane tools, and submit them with submit_evidence_step_artifacts before advancing the batch.`
  `Seed execution_context from the built plan, then prefer advance_investigation_runtime only after the external-preferred steps for that active batch have been submitted or when the batch is planner-owned only.`
  `Call advance_investigation_runtime with incident=<same build request> and execution_context=<seeded or returned execution_context>; do not call it with only batch_id.`
  `If advance_investigation_runtime returns a next_active_batch that clearly asks for one more bounded follow-up evidence batch, advance it once more.`
  `Treat execute_investigation_step and update_investigation_plan as lower-level fallback/debug primitives rather than the preferred runtime-progress path.`
  `Use render_investigation_report late as the canonical final report tool.`
  `Treat Kubernetes MCP and Prometheus MCP as first-class evidence planes when they are the most direct bounded source of evidence.`
- Return the agent's result directly.
- Treat this skill as read-only investigation help for follow-up clarification. Do not take write actions, cleanup actions, or mutation requests through this skill.
- If no arguments were supplied, ask the user what they want investigated.
