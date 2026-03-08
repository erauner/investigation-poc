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
  `Use build_alert_investigation_report as the top-level report entrypoint.`
  `Treat only identity fields such as namespace, pod, service, deployment, node, and container as workload identity.`
  `Treat source or monitoring fields such as prometheus, alertmanager, rule_group, generatorURL, datasource, and runbook_url as metadata, not as workload identity.`
  `Never derive a workload namespace from source or monitoring metadata.`
  `If live runtime evidence disagrees with the alert payload, call out the mismatch explicitly as possible stale alert metadata or drift between alert time and current state.`
  `Return exactly these five sections and no extra appendix sections: Diagnosis, Evidence, Related Data, Limitations, Recommended next step.`
- As a secondary debug-only fallback, also accept `alertname=PodCrashLooping` or `alertname: PodCrashLooping`.
- Only treat the request as alert-shaped when one of those explicit alert forms is present.
- Do not treat `Backend/<name>`, `Frontend/<name>`, or `Cluster/<name>` as alert names.
- Otherwise set the wrapper header to:
  `[INVESTIGATION_ENTRYPOINT]=generic`
  `Use build_investigation_report as the top-level report entrypoint.`
  `If the target is vague, resolve it first with find_unhealthy_pod before calling build_investigation_report.`
- Return the agent's result directly.
- Treat this skill as read-only investigation help for follow-up clarification. Do not take write actions, cleanup actions, or mutation requests through this skill.
- If no arguments were supplied, ask the user what they want investigated.
