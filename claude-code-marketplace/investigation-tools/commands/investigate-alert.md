---
description: Investigate a Kubernetes alert with the alert-specific triage path.
argument-hint: [alert text]
allowed-tools: mcp__kagent__invoke_agent
---

Use the `mcp__kagent__invoke_agent` tool.

- Set `agent` to `kagent/homelab-k8s-custom-agent`.
- Treat this command as the alert-specific investigation path.
- Build `task` as this deterministic wrapper, then append the user's arguments verbatim under `Original user request:`.
- Wrapper header:
  `[INVESTIGATION_ENTRYPOINT]=alert`
  `Use build_alert_investigation_report as the top-level report entrypoint.`
- Add these instructions in the wrapper before the original request:
  `Treat the pasted content below as alert text to extract, not as a workload target string.`
  `Extract alertname, labels, annotations, namespace, pod, service, instance, severity, and status from the pasted alert text before calling build_alert_investigation_report.`
  `If the pasted text includes Labels: or Annotations: sections, use those values as the authoritative alert fields.`
  `Do not investigate the first freeform words of the pasted message as the target unless they are explicitly a Kubernetes object reference such as pod/<name> or service/<name>.`
- Preserve any alert details the user included in the original request, such as the alert name, namespace, pod, service, labels, or annotations.
- Do not rewrite `Backend/<name>`, `Frontend/<name>`, or `Cluster/<name>` into alert names.
- Return the agent's result directly.
- Treat this command as read-only investigation help. Do not take write actions, cleanup actions, or mutation requests through this command.
- If no arguments were supplied, ask the user what they want investigated.
