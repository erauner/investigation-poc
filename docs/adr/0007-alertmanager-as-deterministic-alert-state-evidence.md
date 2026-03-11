# ADR 0007: Add Alertmanager As Deterministic Alert-State Evidence For Local Validation

- Status: Proposed
- Date: 2026-03-11
- Related ADRs:
  - `docs/adr/0001-artifact-oriented-investigation-workflow.md`
  - `docs/adr/0002-external-step-artifact-submission.md`
  - `docs/adr/0004-bounded-exploratory-evidence.md`
  - `docs/adr/0005-unified-ingress-and-subject-resolution.md`
  - `docs/adr/0006-loki-as-complementary-observability-evidence.md`

## Context

The investigation system can now validate observability-backed behavior locally in kind for:

- Prometheus-backed service evidence and bounded follow-up
- Loki-backed complementary log evidence

Both validations rely on the same architectural pattern:

- a real peer MCP integration
- deterministic fixture shaping
- a kind-only optional observability stack
- validation scripts that assert route provenance and artifact behavior, not only final prose

Alert-driven investigations remain important, but today the local validation story is weaker.
The system can accept alert-shaped requests and preserve alert provenance, yet it does not have a deterministic local mechanism for controlling alert state the way the local metrics and log fixtures control Prometheus and Loki behavior.

That creates a practical test gap:

- operators ask to investigate alerts, not only workloads or services
- alert payload fidelity matters, including:
  - `alertname`
  - labels
  - annotations
  - starts/ends timestamps
  - stale or mismatched targets
- local validation should be able to force:
  - a firing alert
  - a resolved alert
  - a stale alert target
  - multiple simultaneous alerts
  - no matching alerts

Unlike Prometheus and Loki, Alertmanager is primarily discrete state, not rolling evidence reconstruction.
That means deterministic local control is more important than scout behavior in the first slice.

## Decision

The architecture should treat Alertmanager as:

> a complementary alert-state evidence source that enables deterministic local validation of alert-driven workflows, while keeping workload and service evidence owned by the existing runtime planes.

This means:

- Alertmanager should be introduced first as a peer evidence source for alert state, not as a new primary diagnosis plane
- local deterministic alert testing should be built by controlling Alertmanager state directly
- the first slice should preserve current alert-ingress behavior while adding a reliable way to validate alert-state queries and alert provenance end to end
- Alertmanager should initially be additive and corroborating, not a replacement for workload/service evidence

## Why This Is Reasonable Now

Prometheus and Loki already proved the key local-testing pattern:

- optional kind-only observability infrastructure can be stood up
- deterministic fixtures can shape backend state
- peer MCP contracts can be validated live
- route provenance can show preferred versus contributing evidence honestly

Alertmanager can reuse the same structural pattern, but the semantic goal is different.
The main need is controlled alert presence and alert payload fidelity, not bounded range-query improvement.

## First-Slice Scope

The first Alertmanager slice should do four things:

1. add Alertmanager endpoint and MCP settings / registry support
2. add a typed `AlertmanagerMcpClient`
3. add a deterministic local alert injection path
4. add a kind validation lane for alert-driven investigations

The first slice should not:

- create a new bounded scout plane
- replace workload or service evidence as the actual diagnosis route
- depend on complex recording/rule timing if direct alert injection can achieve the same validation outcome more reliably

## Deterministic Local Strategy

The local kind strategy should prefer direct synthetic alert control over rule timing for the initial slice.

That means:

- stand up an optional Alertmanager in kind if one is not already available in the local stack
- inject synthetic alerts through the Alertmanager API
- use deterministic payloads for:
  - labels
  - annotations
  - timestamps
  - grouping shape
  - stale versus current targets

This is preferable to a rule-only approach for the first slice because it gives precise control over:

- alert identity
- firing/resolved transitions
- target mismatch cases
- timing sensitivity

Rule-backed alert generation can be added later if needed to validate monitoring-pipeline realism.

## Shared Versus Non-Shared Logic

### Logic That Should Be Shared

These parts match the Prometheus/Loki pattern:

- peer MCP client structure
- settings and cluster-registry endpoint selection
- route provenance
- external-step artifact submission
- kind optional overlay pattern
- retained-debug validation scripts

### Logic That Should Not Be Shared Blindly

Alertmanager evidence is not metrics evidence and not log evidence.
So these parts should remain alert-specific:

- payload normalization
- alert identity preservation
- alert-state materialization
- stale target handling
- report wording for alert-origin versus runtime-resolved targets

The first slice should not fake symmetry by making Alertmanager look like a time-series or log backend.

## Recommended Evidence Role

The first Alertmanager role should be:

- preserve and query alert-state truthfully
- corroborate that an alert exists or existed
- preserve original alert-derived identity alongside runtime-resolved targets
- improve report truthfulness and alert-entry validation

It should not initially:

- determine the primary diagnosis by itself
- override Prometheus or Kubernetes as the preferred actual route for service/workload evidence

## Validation Goals

The local validation lane should be able to prove all of these:

1. a synthetic alert can be injected and observed via the Alertmanager peer
2. an alert-driven investigation preserves original alert provenance
3. runtime target resolution can diverge from the alert payload and still be reported truthfully
4. no-alert and stale-alert scenarios produce correct limitations
5. Alertmanager appears in route provenance without incorrectly becoming the primary diagnosis route

## Likely First Validation Matrix

The first local matrix should cover:

- firing pod alert
  - e.g. `PodCrashLooping`
- firing service alert
  - e.g. synthetic latency/error alert on `service/api`
- stale target alert
  - alert says `pod/crashy`, runtime resolves `pod/crashy-abc123`
- no matching active alert
  - proves correct limitation behavior

## Consequences

### Positive

- closes the local alert-validation gap
- improves confidence in alert-origin investigations, not only workload/service investigations
- reuses the successful kind validation pattern already proven for Prometheus and Loki
- gives deterministic control over alert payload fidelity

### Negative

- adds another peer contract to maintain
- introduces more optional kind infrastructure
- direct injection can validate alert-state behavior without validating full rule-generation realism

## What This ADR Does Not Decide

This ADR does not decide:

- the exact external Alertmanager MCP server/tool contract
- whether the first slice uses an existing external MCP server or a thin compatibility wrapper
- whether later slices should validate rule-generated alerts instead of direct injection
- whether Alertmanager should ever become a trigger for bounded exploratory follow-up

## Recommended Next Step

The next implementation slice should be:

1. identify or adopt a concrete Alertmanager MCP contract
2. add deterministic local alert injection
3. add a kind validation lane for alert-driven investigations
4. keep Alertmanager additive and provenance-first in the first slice
