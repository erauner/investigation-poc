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

This direction is now partially implemented:

- the control plane can expose the active evidence batch as an execution-facing contract
- externally gathered step artifacts can be submitted and reconciled into canonical planner artifacts
- mixed batches can complete incrementally, with planner-owned steps remaining pending
- the canonical reporting path can now consume reconciled executions before using bounded internal fallback
- a canonical runtime-progress helper can now advance one active batch by reconciling submitted evidence first and auto-running only the remaining same-batch planner-owned steps

This ADR also sets the intended sequencing for a future adapter-facing result envelope:

- first establish external step-artifact submission and reconciliation
- then introduce a canonical `InvestigationOutcome` wrapper on top of the reconciled planner/state/report path

`InvestigationOutcome` is still desirable, but it is not the next foundational contract. The evidence-step handoff is.

Recent live validation refines that conclusion further.

The fine-grained handoff primitives are now correct enough to serve as canonical building blocks, but they are still too choreography-heavy to be the only preferred agent-facing runtime path.

In practice, live runs still frequently mis-shape calls such as:

- `get_active_evidence_batch(plan=..., incident=..., batch_id=...)`
- `submit_evidence_step_artifacts(plan=..., incident=..., submitted_steps=..., batch_id=...)`
- `advance_investigation_runtime(incident=..., execution_context=..., submitted_steps=..., batch_id=...)`

The result is that the low-level contract is valuable for adapters, testing, and debugging, but the preferred agent-facing path should converge on a higher-level batch handoff helper above these primitives.

## Role Model

The target architecture should be understood as three cooperating roles.

### 1. Planner/Reconciler

This remains product-owned and lives in `investigation-mcp-server`.

It is responsible for:

- target resolution
- plan construction
- exposing one bounded active evidence batch
- reconciling submitted artifacts
- updating the plan
- ranking hypotheses
- rendering the final report

This role owns investigation semantics and should remain the authoritative source of plan state.

Target resolution should remain product-owned, but its role should become more explicit over time.

It should continue to:

- preserve the requested target or incident subject
- normalize that subject into a canonical current investigation focus
- resolve scope such as workload, service, or node

And it should increasingly also provide:

- execution-facing target details for the active evidence batch
- the concrete target inputs required by the evidence gatherer for a bounded step

This means target resolution should evolve from an internal normalization step into the authoritative bridge between:

- what the user or alert group asked to investigate
- what the product considers the canonical current investigation focus
- what an external evidence gatherer needs in order to satisfy one bounded evidence step

This should not assume that every investigation begins with exactly one concrete Kubernetes object.

The future model should support a broader investigation subject such as:

- a single alert
- a related group of alerts
- a service symptom
- an operator-owned convenience object
- a node or capacity concern
- another higher-level incident description

From that broader subject, the planner/reconciler can then choose a current canonical target and derive one or more execution-facing targets for bounded evidence gathering.

### 2. Evidence Gatherer

This is the external execution role.

It is responsible for:

- satisfying one bounded evidence step or batch
- using the preferred evidence plane where possible
- collecting runtime or metrics evidence from peer MCP servers
- returning typed artifacts plus route provenance

This role should not own planning semantics or final synthesis.

### 3. Outcome Layer

This is a later adapter-facing wrapper role.

It is responsible for:

- packaging reconciled state for downstream consumers
- exposing stable completion status
- summarizing what was investigated and how the investigation terminated

This role should be built only after the planner/reconciler and evidence-gatherer handoff is canonical.

## Subject, Target, And Execution Target

The future design should distinguish between three related but different ideas.

### 1. Investigation Subject

This is the higher-level thing the incident is about.

Examples:

- a single alert
- a correlated alert group
- a service symptom
- an operator-owned resource
- a vague unhealthy workload report

This is intentionally broader and less resource-specific than the current `target` field.

### 2. Canonical Investigation Target

This is the current planner-selected operational focus for reasoning.

Examples:

- `service/api`
- `deployment/api`
- `node/ip-10-0-0-4`

This may change as the investigation progresses.

### 3. Execution Targets

These are the concrete per-step inputs required by the evidence gatherer.

Examples:

- a specific pod to inspect
- a namespace and workload for event lookup
- a service identity for metrics queries
- a node identity for capacity queries

The planner/reconciler should own the transition from subject to canonical target to bounded execution targets.

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

The fine-grained control-plane toolbox now includes:

- `get_active_evidence_batch`
- `submit_evidence_step_artifacts`
- `advance_investigation_runtime`

These should remain available as canonical low-level building blocks for adapters, testing, and debugging.

However, live validation now shows they should not remain the only preferred agent-facing happy path.

The preferred agent-facing runtime surface should converge on a higher-level helper that:

1. prepares the current active batch with planner-owned state attached
2. accepts externally satisfied evidence artifacts
3. advances only the remaining same-batch planner-owned work
4. returns updated execution context plus the next active batch when more evidence work remains

That higher-level helper now belongs in the intended direction of this ADR as the preferred agent-facing runtime surface, while the fine-grained primitives remain canonical lower-level seams.

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

`InvestigationOutcome` should follow this ADR's submission/reconciliation work, not precede it.

## Target Contract Shape

The next iteration should introduce a typed step-artifact submission boundary.

The exact naming may change, but the required semantics are:

- identify the step being satisfied
- identify the batch being satisfied
- identify the canonical target and any execution-facing target details needed for that step
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

This keeps planning, execution, and reconciliation as explicit stages rather than hidden continuation inside one component.

## Transitional Execution Rule

During transition, internal helper execution may remain as a bounded fallback path.

That means:

- externally submitted artifacts become the preferred path for evidence-plane steps
- internal helper execution remains available when:
  - external satisfaction is unavailable
  - peer evidence planes cannot provide the needed evidence
  - controlled local fallback is explicitly desired

Both paths should converge on the same `StepArtifact` semantics and provenance model.

## Relationship To A Future InvestigationOutcome

This ADR does not reject `InvestigationOutcome`.

It only places it later in the sequence.

Once evidence-plane steps can be satisfied externally and submitted back through a typed reconciliation boundary, a canonical `InvestigationOutcome` becomes much more valuable.

At that point, the outcome envelope can honestly wrap:

- reconciled `InvestigationState`
- `InvestigationAnalysis`
- `InvestigationReport`
- a stable completion status such as:
  - `completed`
  - `partial`
  - `blocked`
  - `failed`
- a compact execution summary derived from submitted and fallback artifacts

That future outcome layer should be:

- adapter-facing
- trigger-agnostic
- stable across Claude Code, Slack-style triggers, and future UI consumers

But it should not be introduced before the evidence-step handoff contract exists, because otherwise it would wrap a still-transitional execution story and make the system look more finished than it is.

## Consequences

### Positive

- the runtime becomes consistent with capability-based planning
- peer evidence planes become operationally real rather than only advisory
- provenance becomes actionable rather than merely diagnostic
- the product layer remains the semantic owner without owning every evidence fetch
- the low-level submission contract remains available for adapters and debugging while the preferred agent path can become simpler and more reliable

### Negative

- orchestration becomes more explicit
- a submission/reconciliation boundary must be designed and tested carefully
- transition will temporarily require supporting both:
  - external artifact submission
  - internal helper fallback
- the fine-grained primitives are not, by themselves, a reliable enough agent-facing happy path, so an additional higher-level helper is warranted

## Implementation Direction

The next implementation slice should likely do the following:

1. define an execution-facing representation of the active evidence batch
2. define a typed submitted-step-artifact request model
3. extend plan update or add a dedicated submission route/tool for externally satisfied evidence steps
4. keep `execute_investigation_step(...)` as bounded fallback execution during transition
5. ensure final rendering consumes reconciled artifacts identically regardless of whether they were submitted externally or executed internally
6. add a higher-level batch handoff helper above the fine-grained primitives so the preferred agent-facing path does not have to reconstruct low-level orchestration repeatedly

The slice after that should likely introduce the adapter-facing `InvestigationOutcome` envelope on top of the now-honest reconciled state.

## Recommended Sequencing

The intended order of implementation is:

1. expose active evidence batches as execution-facing contracts
2. add typed submitted-step-artifact reconciliation
3. add a canonical runtime-progress helper on top of the fine-grained submission flow
4. keep the fine-grained primitives available for adapters, testing, and debugging
5. add a higher-level batch handoff helper for the preferred agent-facing runtime path
6. keep internal helper execution as explicit bounded fallback during transition
7. introduce the adapter-facing `InvestigationOutcome` envelope only after reconciled execution is canonical

This work should also intentionally evolve target resolution so that active evidence steps carry not only a canonical investigation target, but also the concrete execution-facing target details required by the evidence gatherer.

This order matters.

If the outcome envelope is introduced too early, it will wrap a still-transitional execution story and weaken the value of the planner/evidence split.

## Non-Goals

This ADR does not propose:

- re-expanding the public surface with legacy report-first helpers
- adding write actions
- introducing unbounded autonomous loops
- splitting the product semantics across multiple custom MCP backends
- moving normalization, planning, ranking, or rendering out of the product-owned control plane
