---
description: Investigate a Kubernetes alert with the alert-specific triage path.
argument-hint: [alert text]
allowed-tools: mcp__kagent__invoke_agent
---

Use the `mcp__kagent__invoke_agent` tool.

- Set `agent` to `kagent/incident-triage`.
- Treat this command as the alert-specific investigation path.
- Build `task` as this deterministic wrapper, then append the user's arguments verbatim under `Original user request:`.
- Wrapper header:
  `[INVESTIGATION_ENTRYPOINT]=alert`
  `Use the planner-led investigation flow for alert handling.`
  `After extracting alert facts, build_investigation_plan and prefer handoff_active_evidence_batch as the default runtime-progress helper.`
  `Call handoff_active_evidence_batch first with incident=<same build request> and no handoff_token.`
  `If handoff_active_evidence_batch returns next_action=submit_external_steps, use required_external_step_ids to select the matching steps from active_batch.steps.`
  `For each required external step, build one submitted_steps item with step_id=<the contract step id>, actual_route=<the peer MCP server/tool actually used>, and the payload field named by artifact_type from that same step contract.`
  `On the follow-up handoff call, send incident=<same build request>, handoff_token=<returned handoff_token>, and submitted_steps=<the non-empty typed artifacts built from the required external steps>.`
  `Do not call handoff_active_evidence_batch again with an empty submitted_steps list after next_action=submit_external_steps.`
  `If handoff_active_evidence_batch returns next_action=call_handoff_again, call it once more with handoff_token=<returned handoff_token>.`
  `Treat get_active_evidence_batch, submit_evidence_step_artifacts, and advance_investigation_runtime as lower-level fine-grained runtime seams, and treat execute_investigation_step and update_investigation_plan as lower-level fallback/debug primitives.`
  `Use render_investigation_report late as the canonical final report tool for the five-section response.`
  `Use exactly these Markdown headings verbatim: ## Diagnosis, ## Evidence, ## Related Data, ## Limitations, ## Recommended next step.`
- Add these instructions in the wrapper before the original request:
  `Treat the pasted content below as alert text to extract, not as a workload target string.`
  `Extract alertname, labels, annotations, namespace, pod, service, instance, severity, and status from the pasted alert text before using the planner-led investigation path.`
  `If the pasted text includes Labels: or Annotations: sections, use those values as the authoritative alert fields.`
  `Treat only identity fields such as namespace, pod, service, deployment, node, and container as workload identity.`
  `Treat source or monitoring fields such as prometheus, alertmanager, rule_group, generatorURL, datasource, and runbook_url as metadata, not as workload identity.`
  `Never derive a workload namespace from source or monitoring metadata.`
  `If a service or pod label is present but namespace is missing, say the namespace is unknown instead of guessing.`
  `Do not investigate the first freeform words of the pasted message as the target unless they are explicitly a Kubernetes object reference such as pod/<name> or service/<name>.`
  `If live runtime evidence disagrees with the alert payload, call out the mismatch explicitly as possible stale alert metadata or drift between alert time and current state.`
  `Preserve the original alert name and the resolved operational target name explicitly in the final five-section answer when they are present in the request or report evidence.`
  `Also preserve the exact original alert-derived target string verbatim, such as pod/<name>, even if runtime resolution later points to a deployment or a specific replica pod.`
  `Do not rewrite the original alert-derived target string by removing the slash or changing its formatting. Keep forms such as pod/crashy exactly as written.`
  `Return exactly these five sections and no extra appendix sections: Diagnosis, Evidence, Related Data, Limitations, Recommended next step.`
- Preserve any alert details the user included in the original request, such as the alert name, namespace, pod, service, labels, or annotations.
- Do not rewrite `Backend/<name>`, `Frontend/<name>`, or `Cluster/<name>` into alert names.
- Return the agent's result directly.
- Treat this command as read-only investigation help. Do not take write actions, cleanup actions, or mutation requests through this command.
- If no arguments were supplied, ask the user what they want investigated.
