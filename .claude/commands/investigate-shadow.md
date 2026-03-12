---
description: Investigate a Kubernetes workload, service, node, or operator target with the shadow BYO lane.
argument-hint: [task]
allowed-tools: mcp__kagent__invoke_agent
---

Use the `mcp__kagent__invoke_agent` tool.

- Set `agent` to `kagent/incident-triage-shadow`.
- Treat this command as the only primary investigation entrypoint for the shadow lane.
- Build `task` as this deterministic wrapper, then append the user's arguments verbatim under `Original user request:`.
- Before building the wrapper, decide whether the request is an explicit alert-form request.
- Route to alert mode only when the request includes one of these explicit forms:
  - `Investigate alert <AlertName> ...`
  - `alertname=<AlertName>`
  - `alertname: <AlertName>`
- Do not route to alert mode when the candidate contains `/` or starts with `Backend/`, `Frontend/`, or `Cluster/`.
- Do not infer alert mode from vague prose alone.
- If the request is alert-form, use this wrapper header:
  `[INVESTIGATION_ENTRYPOINT]=alert`
  `Treat the pasted content below as alert text to extract, not as a workload target string.`
  `Extract alertname, labels, annotations, namespace, pod, service, instance, severity, and status from the pasted alert text before using the planner-led investigation path.`
  `If the pasted text includes Labels: or Annotations: sections, use those values as the authoritative alert fields.`
  `Treat only identity fields such as namespace, pod, service, deployment, node, and container as workload identity.`
  `Treat source or monitoring fields such as prometheus, alertmanager, rule_group, generatorURL, datasource, and runbook_url as metadata, not as workload identity.`
  `Never derive a workload namespace from source or monitoring metadata.`
  `If a service or pod label is present but namespace is missing, say the namespace is unknown instead of guessing.`
  `Do not investigate the first freeform words of the pasted message as the target unless they are explicitly a Kubernetes object reference such as pod/<name> or service/<name>.`
  `Use the planner-led investigation flow.`
  `After extracting alert facts, prefer run_orchestrated_investigation as the default end-to-end runtime path.`
  `run_orchestrated_investigation keeps batch selection, external-step materialization, advancement, and final rendering in product code.`
  `Treat handoff_active_evidence_batch, get_active_evidence_batch, submit_evidence_step_artifacts, and advance_investigation_runtime as lower-level fine-grained runtime seams for debugging or explicit adapter choreography.`
  `Treat execute_investigation_step and update_investigation_plan as lower-level fallback/debug primitives.`
  `Use render_investigation_report only as a secondary low-level render seam when you are explicitly debugging the staged runtime path.`
  `Use exactly these Markdown headings verbatim: ## Diagnosis, ## Evidence, ## Related Data, ## Limitations, ## Recommended next step.`
  `If live runtime evidence disagrees with the alert payload, call out the mismatch explicitly as possible stale alert metadata or drift between alert time and current state.`
  `Preserve the original alert name and the resolved operational target name explicitly in the final five-section answer when they are present in the request or report evidence.`
  `Also preserve the exact original alert-derived target string verbatim, such as pod/<name>, even if runtime resolution later points to a deployment or a specific replica pod.`
  `Do not rewrite the original alert-derived target string by removing the slash or changing its formatting. Keep forms such as pod/crashy exactly as written.`
  `Return exactly these five sections and no extra appendix sections: Diagnosis, Evidence, Related Data, Limitations, Recommended next step.`
- Otherwise use this wrapper header:
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
- Return the agent's result directly.
- Treat this command as read-only investigation help. Do not take write actions, cleanup actions, or mutation requests through this command.
- If no arguments were supplied, ask the user what they want investigated.
