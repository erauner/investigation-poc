# Investigation Interface Contract

This document defines the shared user-facing surface for the investigation product across:

- Claude Code local command
- Claude Code plugin
- Claude Desktop extension

The goal is to keep transport-specific packaging from changing the product semantics.

## Canonical action

The primary user-facing action is:

- `Investigate`

That action means:

- take a Kubernetes problem description or alert-shaped request
- investigate read-only
- route through the controller + agent + backend path
- return an investigation result for a human operator

## Read-only boundary

The `Investigate` surface is read-only.

It is for:

- investigation
- diagnosis
- explanation
- deployment/pod/service inspection
- alert follow-up

It is not for:

- delete
- patch
- restart
- rollout
- cleanup
- write actions of any kind

Any future mutation surface should use a separate action family with explicitly different names.

## Shared semantics

Across clients, `Investigate` should preserve these semantics:

- default path is `kagent-controller -> incident-triage -> investigation-mcp-server`
- output is investigation-oriented, not mutation-oriented
- cluster-awareness rules are backend-owned
- report composition remains backend-owned
- controller and agent implementation details stay hidden from the primary UX
- planner/control-plane behavior stays product-owned, while direct runtime and metrics evidence may come from peer MCP servers such as Kubernetes MCP and Prometheus MCP

For backend tool surfaces, prefer the planner-led control plane when available:

- generic targeted investigations should prefer `resolve_primary_target`, `build_investigation_plan`, `advance_investigation_runtime`, and `render_investigation_report`
- generic targeted investigations should prefer `resolve_primary_target`, `build_investigation_plan`, `get_active_evidence_batch`, `submit_evidence_step_artifacts`, `advance_investigation_runtime`, and `render_investigation_report`
- alert-shaped investigations should preserve alert routing, but still treat `render_investigation_report` as the canonical final report surface
- `advance_investigation_runtime` is the preferred runtime-progress step after plan creation and should carry forward `execution_context` into any bounded follow-up advance or final render
- when the active batch includes externally preferred evidence steps, callers should first use `get_active_evidence_batch`, satisfy those steps with peer evidence-plane tools, submit them with `submit_evidence_step_artifacts`, and only then advance the batch
- `execute_investigation_step` and `update_investigation_plan` remain lower-level fallback/debug seams rather than the preferred runtime-progress path
- direct logs, events, resource inspection, metrics queries, alert queries, and exemplar lookups should be treated as evidence-plane work, not as replacements for planning or final rendering

The current capability-to-tool policy is:

- `workload_evidence_plane`
  - prefer `kubernetes-mcp-server`
  - use tools such as `pods_log`, `resources_get`, `events_list`, `pods_list_in_namespace`
- `service_evidence_plane`
  - prefer `prometheus-mcp-server`
  - use tools such as `execute_query`, `execute_range_query`
  - fall back to `kubernetes-mcp-server` for runtime inspection
- `node_evidence_plane`
  - prefer `prometheus-mcp-server`
  - use tools such as `execute_query`, `execute_range_query`
  - fall back to `kubernetes-mcp-server` for runtime inspection
- `alert_evidence_plane`
  - stays product-owned for alert extraction and alert-shaped context
- `collect_change_candidates`, `rank_hypotheses`, and `render_investigation_report`
  - stay product-owned on `investigation-mcp-server`

## Client-side routing

Clients should preserve one small, deterministic routing rule before handing work to the controller-backed agent path.

- route to the alert entrypoint only when the request includes an explicit alert form
- otherwise keep the generic entrypoint

Preferred alert form:

- `Investigate alert PodCrashLooping ...`

Debug or structured fallback forms:

- `alertname=PodCrashLooping`
- `alertname: PodCrashLooping`

Guardrails:

- do not treat `Backend/<name>`, `Frontend/<name>`, or `Cluster/<name>` as alert names
- do not infer alert mode from vague prose alone
- preserve the original user request verbatim when wrapping it for the agent

When a client needs to steer the controller-backed agent explicitly, prepend one of these directives:

- `[INVESTIGATION_ENTRYPOINT]=generic`
- `[INVESTIGATION_ENTRYPOINT]=alert`

The wrapper should still include the original user request and should remain read-only.

## User-facing names

Use these names as the primary surface:

- Claude Code local: `/investigate`
- Claude Code local alert path: `/investigate-alert`
- Claude Code plugin: `/investigation-tools:investigate`
- Claude Code plugin alert path: `/investigation-tools:investigate-alert`
- Claude Desktop tool: `investigate`

Do not lead with internal names like:

- `invoke_agent`
- `investigate_with_agent`
- agent refs

Those may exist for compatibility or debugging, but they are not the product surface.

## Optional discovery/debug surfaces

The following are acceptable as secondary/debug surfaces:

- `list_investigation_agents`
- optional skills for auto-discovery support

They should not replace `Investigate` as the primary user entrypoint.

## Shared config naming

Use one conceptual naming family across clients:

- `INVESTIGATION_REMOTE_MCP_URL`
- `INVESTIGATION_REMOTE_MCP_TOKEN`
- `INVESTIGATION_DEFAULT_AGENT_REF`

Desktop can map user settings into these names.
Claude Code plugin config can consume these names through environment expansion.

## Intentional client differences

These differences are expected and acceptable.

### Claude Code

- local project command for fast iteration
- plugin packaging for sharing
- optional skills or subagents for additive automation

### Claude Desktop

- `.mcpb` packaging
- local stdio runtime
- settings UI through the extension manifest

These transport differences should not change the meaning of `Investigate`.
