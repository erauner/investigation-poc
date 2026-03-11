# ADR 0006: Add Loki As Complementary Observability Evidence, Not A Replacement Metrics Plane

- Status: Proposed
- Date: 2026-03-11
- Related ADRs:
  - `docs/adr/0001-artifact-oriented-investigation-workflow.md`
  - `docs/adr/0002-external-step-artifact-submission.md`
  - `docs/adr/0004-bounded-exploratory-evidence.md`
  - `docs/adr/0005-unified-ingress-and-subject-resolution.md`

## Context

The current investigation runtime already treats Prometheus-backed evidence as a first-class peer evidence source in several places:

- capability policy selects Prometheus as the preferred peer server for service and node evidence
- bounded service follow-up can run a Prometheus range-query scout when baseline evidence is weak
- adequacy and improvement logic understand metric-backed evidence as a real way to improve an artifact
- route provenance and reporting can truthfully show whether Prometheus or Kubernetes was the winning evidence path

This is stronger than simply having a metrics adapter.
Prometheus is part of the evidence-plane contract.

Loki is available in the broader observability environment, but the investigation system does not currently model it as a first-class peer evidence source.
Today, logs appear mainly through Kubernetes runtime collection and product-owned materialization.

That leaves a practical gap:

- Prometheus answers whether service behavior is degraded
- Loki can answer what the service or workload was saying while degraded
- Kubernetes pod-log collection can answer part of that, but only when the runtime pod is obvious and the local pod logs are the right slice

Loki therefore overlaps with existing log evidence, but it is not identical to:

- Kubernetes pod log collection
- Prometheus metrics queries

The design question is not whether Loki is useful.
It is how to add it without:

- turning the runtime into arbitrary observability-tool choreography
- duplicating the Prometheus lane with a fake symmetry that hides real semantic differences
- moving semantic ownership out of product-owned evidence materialization and adequacy rules

## Decision

The architecture should treat Loki as:

> a complementary peer evidence source that can share execution, provenance, and submission scaffolding with Prometheus, while keeping its own evidence semantics and scout rules.

This means:

- Loki should reuse the existing peer-MCP transport pattern, route provenance pattern, typed artifact submission pattern, and bounded-exploration framing where possible
- Loki should not be modeled as "metrics but from logs"
- Loki should not replace Prometheus as the preferred primary evidence source for service or node health
- Loki should first be introduced where it naturally improves weak investigations:
  - workload evidence
  - service evidence follow-up
- Loki-backed evidence must remain product-materialized into typed findings and evidence items before adequacy or ranking decisions consume it

## Shared Versus Non-Shared Logic

Some of the Prometheus path should be shared with Loki.
Some should stay separate.

### Logic That Should Be Shared

These parts are transport and orchestration concerns, not Prometheus-specific semantics:

- peer MCP client structure
  - one typed client per peer server
  - same session lifecycle
  - same MCP error handling style
- route provenance
  - requested capability
  - attempted routes
  - actual route
  - contributing routes
- bounded scout framing
  - budget accounting
  - stop reasons such as:
    - `probe_failed`
    - `probe_not_improving`
    - `probe_improved_artifact`
- artifact submission and reconciliation
  - external-step artifacts remain the canonical exchange seam
- cluster-scoped endpoint resolution
  - cluster registry should be able to carry observability endpoint selection for more than one backend

In practice, the existing Prometheus seams show the reusable pattern:

- policy in `src/investigation_service/execution_policy.py`
- peer transport in `src/investigation_orchestrator/mcp_clients.py`
- bounded follow-up in `src/investigation_orchestrator/service_scout.py`
- adequacy / improvement in `src/investigation_service/adequacy.py`

Those seams should become more observability-generic where it is structurally helpful.

### Logic That Should Not Be Shared Blindly

These parts are semantically different between metrics and logs:

- query construction
  - PromQL family selection is not LogQL selection
- evidence extraction
  - a scalar metric and a set of matching log lines are different evidence shapes
- adequacy thresholds
  - "one usable metric family" is not equivalent to "some matching log lines"
- scout improvement rules
  - a Loki follow-up should improve an artifact for different reasons than a range-metric scout
- primary-source preference by capability
  - service and node health should remain metrics-first unless there is a deliberate future ADR changing that

So the reusable structure should be shared, but the domain meaning should remain backend-specific.

## Recommended Initial Scope

The first Loki slice should be additive and narrow:

1. add Loki endpoint and MCP settings / cluster-registry support
2. add a `LokiMcpClient` parallel to `PrometheusMcpClient`
3. use Loki first as a complementary workload/service evidence source
4. materialize Loki results into typed evidence items before analysis
5. add bounded Loki follow-up only after the basic materialization path is working

This first slice should not:

- create a new top-level planner capability immediately
- replace service metrics-first behavior
- make Loki mandatory for baseline investigations
- attempt multicluster Loki routing before the existing Prometheus multicluster limitation is addressed

## Capability Direction

The near-term direction should be:

- `workload_evidence_plane`
  - allow Loki as an additional preferred or fallback peer evidence source for workload-specific log discovery
- `service_evidence_plane`
  - keep Prometheus as the preferred primary source
  - allow Loki as a bounded follow-up source when service metrics are weak, contradictory, or insufficiently explanatory
- `node_evidence_plane`
  - do not prioritize Loki initially

This preserves the current metrics-first service behavior while still allowing richer service diagnosis when metrics alone are not enough.

## Data Model Direction

The current model source vocabulary includes:

- `k8s`
- `events`
- `logs`
- `prometheus`
- `heuristic`

The first Loki slice may use `logs` as the evidence source label if needed for compatibility, but the architecture should prefer introducing explicit Loki provenance rather than hiding it forever behind generic `logs`.

That means the likely intended direction is:

- route provenance names the actual MCP/tool path as Loki
- evidence items may initially remain `source="logs"` for compatibility
- a later cleanup may introduce explicit `source="loki"` if product/reporting value justifies the model change

## Cluster And Settings Direction

The cluster registry currently carries `prometheus_url`.
If Loki becomes a real peer evidence source, the same layer should be extended to carry Loki endpoint information rather than hiding it in ad hoc environment configuration.

That implies likely additive fields such as:

- `loki_url`
- possibly `loki_mcp_url` in settings, if Loki is exposed through its own MCP peer

This is an additive extension of the existing cluster-resolution seam, not a new registry concept.

## Bounded Exploration Direction

If Loki gets bounded follow-up behavior, it should follow ADR 0004's rule:

- deterministic spine stays unchanged
- exploration happens only inside approved evidence-plane seams
- scout outcomes stay typed and auditable

Likely Loki-specific probe kinds would be separate from `service_range_metrics`, for example:

- `service_correlated_logs`
- `workload_related_logs`

Those should remain separate probe kinds because they improve artifacts for different reasons than range metrics.

## Consequences

### Positive

- richer operator-facing evidence when metrics alone are not explanatory
- reuses the proven peer-MCP and bounded-scout architecture rather than inventing a second control path
- keeps semantic ownership in product code
- improves observability flexibility without reopening arbitrary tool choreography

### Negative

- more observability-specific policy and adequacy logic to maintain
- another peer transport to test and validate
- more cluster/config surface to keep aligned
- possible pressure to overfit service investigations toward log-heavy evidence when metrics would be more reliable

## What This ADR Does Not Decide

This ADR does not decide:

- the exact Loki MCP server/tool contract
- whether the first Loki evidence source label should be `logs` or a new explicit `loki` enum
- whether future service investigations should ever become Loki-first instead of Prometheus-first
- multicluster Loki routing
- Grafana/Tempo integration

## Recommended Next Step

The next implementation slice should be:

> add Loki as an additive complementary peer evidence source for workload and service investigations by reusing the existing peer-client, provenance, and bounded-scout scaffolding while keeping adequacy and evidence semantics backend-specific.
