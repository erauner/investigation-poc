# ADR 0002: Move Evidence-Plane Execution Toward External Step-Artifact Submission

- Status: Proposed
- Date: 2026-03-09
- Related ADR: `docs/adr/0001-artifact-oriented-investigation-workflow.md`

## Context

ADR 0001 moved the investigation system toward a planner-led, artifact-oriented workflow.

That direction is now materially in place:

- the public control plane is narrow
- planning is explicit
- execution is batch-oriented
- state and rendering are artifact-backed
- peer evidence planes now exist in the local runtime:
  - `kubernetes-mcp-server`
  - `prometheus-mcp-server`

The remaining architectural gap is no longer public-surface cleanup.

The remaining gap is that the system is still hybrid at evidence execution time:

- `PlanStep` can describe capability intent
  - requested capability
  - preferred MCP server
  - preferred tool names
  - fallback MCP server
  - fallback tool names
- but `execute_investigation_step(...)` still satisfies workload, service, and node evidence steps through internal product-owned helpers

In practice, the current execution path is still:

1. build plan
2. select active evidence batch
3. execute step inside `investigation-mcp-server`
4. call internal helpers such as:
   - `collect_workload_evidence(...)`
   - `collect_service_evidence(...)`
   - `collect_node_evidence(...)`
   - `collect_alert_evidence(...)`
5. return `StepArtifact`

That means the planner can express peer evidence-plane intent, but the backend still performs the evidence collection itself for most evidence steps.

Recent provenance work makes this mismatch visible:

- each executed `StepArtifact` now records:
  - requested capability
  - actual route used
  - route satisfaction
- `ToolPathTrace` now exposes step-level provenance in final state and reports

This is useful because it makes the current hybrid state explicit.

It also makes the next move clearer:

> The system now needs an execution handoff contract, not more hidden helper execution.

## Decision

We will move the evidence-plane workflow toward external step-artifact submission.

The intended direction is:

- `investigation-mcp-server` remains the product-owned control plane
- peer evidence planes remain the preferred execution surfaces for runtime and metrics evidence
- evidence-plane steps should be satisfiable by an external orchestrator or agent
- the product layer should reconcile submitted step artifacts rather than owning all evidence collection internally

This means the long-term shape should be:

1. resolve target
2. build plan
3. expose one bounded active evidence batch
4. have the orchestrator satisfy those evidence steps through the appropriate evidence plane
5. submit typed artifacts plus provenance back to the product-owned control plane
6. update the plan
7. repeat at most one bounded batch at a time
8. render late from reconciled artifacts

The key shift is:

> Treat peer evidence execution as a first-class submitted artifact flow, not as an implicit backend implementation detail.

## What Stays Product-Owned

The following responsibilities remain inside `investigation-mcp-server`:

- alert-aware target normalization
- target resolution
- plan construction
- plan updates and bounded execution semantics
- artifact reconciliation
- correlated change ranking
- hypothesis ranking
- final report rendering

The product layer still owns investigation semantics.

This ADR does not move those semantics into peer MCP servers.

## What Moves Toward External Satisfaction

The following evidence-plane capabilities should move toward orchestrator- or agent-satisfied execution:

- `workload_evidence_plane`
- `service_evidence_plane`
- `node_evidence_plane`

Likely tool ownership remains:

- Kubernetes runtime evidence:
  - `kubernetes-mcp-server`
  - logs
  - events
  - workload and pod inspection
  - namespace-scoped resource lookup
- metrics and service/node evidence:
  - `prometheus-mcp-server`
  - targeted queries
  - range queries
  - alerts
  - rules
  - targets
  - exemplars

Alert extraction remains transitional and product-owned for now.

## Why

This shift aligns the runtime with the architecture we already claim to have:

- plans already describe capabilities rather than old helper names
- agent policy already exposes peer evidence planes explicitly
- tool-path provenance already distinguishes planner intent from actual execution

Without this shift, the system remains in an awkward state:

- the planner says peer MCP first
- the agent prompt says peer MCP first
- but the backend still executes internal evidence helpers

That is acceptable during transition, but it should not become the long-term design.

The execution handoff should be made explicit because it improves:

- routing honesty
- observability
- replayability
- testability
- separation of concerns
- future compatibility with richer A2A orchestration patterns

## Alternatives Considered

### 1. Keep Internal Helper Execution as the Long-Term Design

We are not choosing this.

Why not:

- it leaves peer evidence planes as mostly advisory
- it keeps the most important execution truth hidden behind backend helpers
- it weakens the value of capability-based planning

### 2. Add a Backend-Side Peer MCP Dispatcher First

We are not choosing this as the first move.

Why not:

- it would re-centralize evidence execution inside `investigation-mcp-server`
- it increases transport and operational coupling
- it blurs the separation between control plane and evidence plane

This may still be useful later for specific fallback or managed-runtime cases, but it should not be the primary next step.

### 3. Add a New Outcome Envelope First

We are not choosing this as the next move.

Why not:

- an outer envelope is less valuable while the execution handoff remains implicit
- routing truth and artifact submission are the more important missing contracts

## Target Contract Shape

The next iteration should introduce a typed step-artifact submission boundary.

The exact naming may change, but the required semantics are:

- identify the step being satisfied
- identify the batch being satisfied
- describe the artifact type
- carry the evidence artifact payload
- carry actual route metadata
- declare whether the route was preferred, fallback, unmatched, or not applicable
- allow the control plane to update plan state from submitted artifacts

Conceptually, the needed contract looks like:

1. planner emits an executable evidence-step contract
2. orchestrator satisfies the step using peer evidence tools
3. orchestrator submits a typed artifact
4. control plane reconciles and updates the plan

This is analogous to how some editing systems separate:

- plan or change intent
- execution
- submitted result
- reconciliation

That is the same pattern we want for investigation evidence collection.

## Transitional Execution Rule

During transition, internal helper execution may remain as a bounded fallback path.

That means:

- externally submitted artifacts become the preferred path for evidence-plane steps
- internal helper execution remains available when:
  - external satisfaction is unavailable
  - peer evidence planes cannot provide the needed evidence
  - controlled local fallback is explicitly desired

Both paths should converge on the same `StepArtifact` semantics and provenance model.

## Consequences

### Positive

- the runtime becomes consistent with capability-based planning
- peer evidence planes become operationally real rather than only advisory
- provenance becomes actionable rather than merely diagnostic
- the product layer remains the semantic owner without owning every evidence fetch

### Negative

- orchestration becomes more explicit
- a submission/reconciliation boundary must be designed and tested carefully
- transition will temporarily require supporting both:
  - external artifact submission
  - internal helper fallback

## Implementation Direction

The next implementation slice should likely do the following:

1. define an execution-facing representation of the active evidence batch
2. define a typed submitted-step-artifact request model
3. extend plan update or add a dedicated submission route/tool for externally satisfied evidence steps
4. keep `execute_investigation_step(...)` as bounded fallback execution during transition
5. ensure final rendering consumes reconciled artifacts identically regardless of whether they were submitted externally or executed internally

## Non-Goals

This ADR does not propose:

- re-expanding the public surface with legacy report-first helpers
- adding write actions
- introducing unbounded autonomous loops
- splitting the product semantics across multiple custom MCP backends
- moving normalization, planning, ranking, or rendering out of the product-owned control plane

