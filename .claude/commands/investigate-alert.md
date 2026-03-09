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
  `After extracting alert facts, build_investigation_plan, execute one bounded evidence batch with execute_investigation_step, and update the plan with update_investigation_plan.`
  `If the updated plan clearly asks for one more bounded follow-up evidence batch, execute it once and update the plan again.`
  `Use render_investigation_report late as the canonical final report tool for the five-section response.`
  `Treat Kubernetes MCP and Prometheus MCP as first-class evidence planes when they are the most direct bounded source of evidence.`
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
  `Return exactly these five sections and no extra appendix sections: Diagnosis, Evidence, Related Data, Limitations, Recommended next step.`
- Preserve any alert details the user included in the original request, such as the alert name, namespace, pod, service, labels, or annotations.
- Do not rewrite `Backend/<name>`, `Frontend/<name>`, or `Cluster/<name>` into alert names.
- Return the agent's result directly.
- Treat this command as read-only investigation help. Do not take write actions, cleanup actions, or mutation requests through this command.
- If no arguments were supplied, ask the user what they want investigated.
