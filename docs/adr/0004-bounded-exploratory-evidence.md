# ADR 0004: Keep A Deterministic Spine And Add Bounded Exploratory Evidence Nodes

- Status: Proposed
- Date: 2026-03-10
- Related ADRs:
  - `docs/adr/0001-artifact-oriented-investigation-workflow.md`
  - `docs/adr/0002-external-step-artifact-submission.md`
  - `docs/adr/0003-langgraph-execution-shell.md`

## Context

The planner-led runtime and LangGraph execution shell work completed two important things:

- the model no longer owns the main orchestration loop
- hosted shadow validation now proves that a BYO LangGraph lane can run end to end against real homelab workloads

That makes the next design question more precise.

The current orchestrated path is safer and more predictable than the older declarative prompt-owned path, but it can still feel less capable during evidence gathering. The declarative lane sometimes appears stronger because it can opportunistically:

- inspect one more event stream
- fetch one more related runtime signal
- elaborate chronology more freely
- adapt evidence collection when the first signal is weak

That flexibility is useful in Kubernetes troubleshooting, but restoring it by reopening the whole runtime to raw prompt-owned tool choreography would be the wrong regression.

The system now needs a design rule that preserves:

- product-owned semantics
- bounded runtime behavior
- typed artifact submission
- read-only safety
- auditability

while reintroducing some evidence-gathering flexibility in places where it genuinely improves diagnosis.

## Decision

The next architectural direction is:

> Keep the investigation runtime spine deterministic, and add bounded exploratory evidence behavior only inside approved evidence-plane nodes or subgraphs.

This means:

- `investigation_service` remains the semantic owner
- `investigation_orchestrator` remains the reusable runtime layer
- LangGraph remains the execution shell
- flexible discovery is allowed only inside explicitly approved evidence-gathering seams

The runtime should now be thought of as:

- deterministic spine
  - target resolution
  - plan construction
  - active-batch progression
  - submission reconciliation
  - ranking
  - final report semantics
- bounded-flexible evidence layer
  - selective evidence discovery inside a step
  - bounded follow-up routing when evidence is weak or contradictory
  - presentation depth and rendering profile selection
- explicitly non-flexible areas
  - arbitrary raw tool choreography across the whole investigation
  - write actions
  - unbounded loops
  - moving semantic ownership into prompt logic or the host wrapper

Presentation profiles are a separate downstream concern.

Profiles such as:

- `operator_summary`
- `incident_report`
- `debug_trace`
- `explain_more`

may change how investigation results are rendered, but they must not change:

- evidence adequacy
- batch progression
- reconciliation
- canonical investigation semantics

## What "Bounded Exploratory Evidence" Means

Bounded exploratory evidence is allowed only when all of the following remain true:

- tool use is allowlisted by product-owned policy
- execution has hard budgets
  - max tool calls
  - max time
  - max evidence volume if needed
- the output still materializes into the same typed contract:
  - `SubmittedStepArtifact`
  - `EvidenceBundle`
- provenance remains inspectable
- the outer orchestration spine still decides stop/continue behavior deterministically
- the server/tool allowlist does not expand dynamically at runtime

So the flexibility lives inside a bounded box, not across the whole workflow.

Exploratory behavior may change how evidence is gathered, but not how evidence is represented.

The downstream planner and reconciliation seams must still consume the same typed contracts:

- `SubmittedStepArtifact`
- `EvidenceBundle`
- `StepArtifact`

This ADR does not require that bounded exploration be implemented by an LLM-driven scout immediately.

Bounded exploratory evidence may later be implemented by:

- policy-guided branching
- deterministic probe selection
- structured tool-planning logic
- or a constrained tool-using agent

The architectural requirement is:

- boundedness
- allowlisting
- typed outputs
- provenance

not any one specific mechanism.

## Preferred Pattern

The preferred near-term pattern is:

1. deterministic baseline evidence collection
2. deterministic adequacy evaluation
3. optional bounded evidence-scout subgraph for approved evidence planes
4. deterministic artifact materialization
5. deterministic batch progression and rendering

This is intentionally narrower than making the whole investigation graph agentic.

## Adequacy Outcome Taxonomy

The adequacy gate is the first extension seam for bounded exploration.

The long-term adequacy contract should distinguish at least these outcomes:

- `adequate`
- `weak`
- `contradictory`
- `blocked`

Not every slice needs all four outcomes immediately, but this is the intended conceptual taxonomy.

Why this matters:

- `adequate` means baseline evidence is strong enough to materialize and continue
- `weak` means the evidence is thin and bounded exploration may help
- `contradictory` means the evidence conflicts and bounded follow-up may be needed
- `blocked` means collection limitations dominate and the system should degrade honestly rather than pretending evidence is complete

## Initial Scope

The first place to apply this is:

- `workload_evidence_plane`

Why workload first:

- it is the most mature evidence path today
- it is where declarative-path flexibility has been most obviously useful
- it has the clearest bounded tool set
  - `resources_get`
  - `events_list`
  - `pods_log`
  - `pods_list_in_namespace`

The following should remain deterministic for now:

- `alert_evidence_plane`
- `collect_change_candidates`
- `rank_hypotheses`
- `render_investigation_report`

The following may later adopt bounded exploratory behavior after workload proving:

- `service_evidence_plane`
- `node_evidence_plane`

## Desired State

The desired state is not "more agentic everywhere."

The desired state is:

- a deterministic orchestration spine
- selected exploratory nodes with strict budgets and allowlists
- optional bounded follow-up routing
- rendering profiles that change presentation without changing investigation semantics

Representative examples of desired future behavior:

1. if baseline workload evidence is already strong, materialize immediately
2. if baseline workload evidence is weak, let a bounded scout choose one or two more approved probes
3. if evidence is still insufficient, return a partial but honest artifact
4. if service or node follow-up is needed, route only through pre-approved graph branches

Promotion to a preferred BYO/default hosted runtime is orthogonal to this ADR.

Bounded exploratory evidence can be introduced regardless of whether the preferred hosted runtime:

- remains declarative for a period
- or later shifts to BYO LangGraph hosting

## Implementation Direction

The next implementation slices should follow this order:

1. add step-level flexibility policy in product-owned execution policy
2. add deterministic evidence-adequacy evaluation
3. add a bounded workload evidence-scout subgraph
4. route the main orchestrator to use that subgraph only when adequacy fails
5. add presentation profiles as a separate downstream concern

## Observability And Provenance Expectations

As exploratory behavior grows, observability must grow with it.

The system should preserve enough traceability to explain:

- why exploration was entered
- which bounded probe path actually ran
- what budget was consumed
- what final route or routes satisfied the step

The current `actual_route` seam remains important, but exploratory behavior may require richer provenance and runtime trace detail later.

## Consequences

### Positive

- preserves the guard rails earned by the planner-led transition
- reintroduces useful evidence flexibility without returning to raw prompt choreography
- keeps checkpointing, provenance, and runtime visibility meaningful
- gives LangGraph a more valuable role than just hosting a narrow deterministic loop

### Negative

- adds another runtime-policy dimension to test and maintain
- requires explicit budget and stop-condition design
- increases the importance of checkpoint and observability quality inside evidence subgraphs

## What This ADR Does Not Decide

This ADR does not decide:

- whether the BYO shadow lane should become the default hosted runtime
- when kagent-backed checkpointing should become the default checkpoint mode
- whether service and node evidence should become bounded-exploratory immediately
- whether public resume APIs should exist

The narrower decision is:

> The next flexibility step should be bounded exploratory evidence inside selected runtime nodes, not a return to free-form prompt-owned orchestration.
