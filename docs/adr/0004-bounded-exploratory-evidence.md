# ADR 0004: Keep A Deterministic Spine And Add Bounded Exploratory Evidence Nodes

- Status: Accepted (incremental rollout)
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
  - bounded graph follow-up routing when evidence is weak or contradictory
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

## Updated Rollout Reality

The original intended proving order for bounded exploration was:

- workload first
- service later
- node later

That rollout order has already shifted in implementation:

- deterministic service-alert evidence hardening landed before exploratory generalization was complete
- a bounded service follow-up scout landed early because it closed a real product-quality gap
- bounded node scout work is now in progress

This changes rollout order, not architecture.

The durable architectural rule remains:

- the investigation spine stays deterministic
- exploratory behavior stays fenced inside approved evidence-plane seams
- typed artifact and reconciliation contracts stay unchanged

## Baseline Hardening Versus Exploratory Evidence

Bounded exploratory evidence is not the same thing as improving deterministic baseline evidence collection.

The system should continue to improve:

- deterministic baseline evidence quality
- alert-specific evidence shaping
- target-specific evidence coverage
- deterministic peer transport and fallback behavior

Exploratory behavior begins only after:

- baseline evidence has been gathered
- adequacy has been evaluated
- a bounded scout or bounded follow-up seam is entered

This distinction matters because baseline hardening improves the default path, while exploratory behavior spends extra bounded runtime only when the baseline is weak, contradictory, or blocked.

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

Exploratory behavior may add an intermediate scout-local probe ledger or result summary for observability, but that seam remains transient.

The downstream contracts still remain:

- `SubmittedStepArtifact`
- `EvidenceBundle`
- planner-owned reconciliation into `StepArtifact` and `EvidenceBatchExecution`

This ADR does not require that bounded exploration be implemented by an LLM-driven scout immediately.

Bounded exploratory evidence should prefer simpler mechanisms first, and may later be implemented by:

- deterministic policy-guided branching
- deterministic probe selection
- structured tool-planning logic
- a constrained tool-using agent only if simpler bounded mechanisms are insufficient

The architectural requirement is:

- boundedness
- allowlisting
- typed outputs
- provenance

not any one specific mechanism.

Exploration policy, adequacy thresholds, and allowlists are product-owned policy inputs.
Exploratory runtime nodes consume them; they do not define them.

## Scout Policy And Leading Context

Bounded exploratory seams may consume a small, product-owned leading-context bundle derived from:

- static scout policy
- step- or plane-specific policy
- small runtime-derived hints from baseline evidence and adequacy
- planner-emitted execution-facing step inputs

Illustratively, that seam may look like:

```python
@dataclass(frozen=True)
class ExploratoryNodeContext:
    policy: ExploratoryScoutPolicy
    hints: ScoutHints
    baseline_summary: BaselineEvidenceSummary
    step_id: str
    requested_capability: str
    execution_inputs: StepExecutionInputs
```

The important point is not any one exact type name.
The important point is that bounded exploratory nodes should consume:

- structured policy
- structured runtime hints
- canonical execution inputs already emitted by product-owned planning

This seam is:

- transient runtime input
- product-owned
- small and inspectable

It is not:

- a second semantic model beside `ReportingExecutionContext`
- user-authored free-form prompt tuning
- host-wrapper-owned troubleshooting logic

The baseline summary is intentionally separate from the full evidence artifact.
Exploratory nodes should receive a compact view of what is already known and what is still missing rather than depending on arbitrary prompt reconstruction from raw runtime state.

## Exploratory Node Input And Output Seams

Bounded exploratory nodes should consume a small typed runtime input contract rather than the full investigation state.

That runtime input should be derived from:

- the active step's execution-facing inputs
- product-owned exploration policy
- adequacy-derived hints
- optionally a compact baseline evidence summary

This seam is a transient runtime input contract, not a second semantic model beside `ReportingExecutionContext`.

Exploratory execution may also produce a bounded intermediate probe/result record before deterministic materialization into canonical typed artifacts.

That intermediate seam may capture things such as:

- probes attempted
- which probes were useful, empty, contradictory, or failed
- additional evidence recovered
- additional limitations discovered
- budget consumed and stop reason

The important invariant is that exploratory nodes do not directly redefine planner or report semantics.
They still terminate in the same deterministic materialization path into canonical typed step artifacts.

## Preferred Pattern

The preferred near-term pattern is:

1. deterministic baseline evidence collection
2. deterministic adequacy evaluation
3. optional bounded evidence-scout subgraph for approved evidence planes
4. deterministic artifact materialization
5. deterministic batch progression and rendering

This is intentionally narrower than making the whole investigation graph agentic.

The flexibility lives only in exploratory probe selection:

- which approved probe to try next
- the order of those approved probes
- when the bounded scout has seen enough and should stop

The flexibility does not extend to:

- changing the overall investigation plan
- inventing new tool families or MCP servers
- changing artifact or reconciliation contracts
- continuing beyond bounded budgets

This is distinct from bounded graph follow-up routing.
Exploratory probe selection is step-local behavior inside an approved scout seam.
Bounded graph follow-up routing is main-graph branching only across pre-approved steps or batches.

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

These outcomes are the deterministic control seam between:

- immediate artifact materialization
- bounded exploration
- honest partial degradation

## Initial Scope

The first proving model for this pattern is:

- `workload_evidence_plane`

Why workload was the first intended proving case:

- it is the most mature evidence path
- it is where declarative-path flexibility was most obviously useful
- it has the clearest bounded tool set
  - `resources_get`
  - `events_list`
  - `pods_log`
  - `pods_list_in_namespace`

Workload is the first canonical proving model for bounded exploratory evidence.
Service-specific bounded follow-up may still land earlier in isolated cases where product-quality gaps justify it.
That changes rollout order, not architectural scope.

The following should remain deterministic for now:

- `alert_evidence_plane`
- `collect_change_candidates`
- `rank_hypotheses`
- `render_investigation_report`

- broad service and node exploration should still be policy-gated and incremental rather than assumed everywhere by default

Service and node evidence may adopt the same bounded pattern incrementally where product-quality needs justify it, but that does not change the architectural rule that the flexibility stays local, budgeted, typed, and inspectable.

## Desired State

The desired state is not "more agentic everywhere."

The desired state is:

- a deterministic orchestration spine
- selected exploratory nodes with strict budgets and allowlists
- optional bounded follow-up routing
- rendering profiles that change presentation without changing investigation semantics

Representative examples of desired future behavior:

1. if baseline evidence is already strong, materialize immediately
2. if baseline evidence is weak or contradictory, let a bounded scout choose one or two more approved probes
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
3. formalize the bounded scout input seam with policy, runtime hints, and a compact baseline evidence summary
4. add bounded scout paths for approved evidence planes
5. route the main orchestrator to use those scout paths only when adequacy fails
6. add presentation profiles as a separate downstream concern

## Observability And Provenance Expectations

As exploratory behavior grows, observability must grow with it.

The system should preserve enough traceability to explain:

- why exploration was entered
- which bounded probe path actually ran
- what budget was consumed
- why the scout stopped
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
- exactly which service and node seams should adopt bounded exploration next
- whether public resume APIs should exist

The narrower decision is:

> The next flexibility step should be bounded exploratory evidence inside selected runtime nodes, not a return to free-form prompt-owned orchestration.
