# ADR 0003: Use LangGraph As The Future Execution Shell, Not The Semantic Owner

- Status: Proposed
- Date: 2026-03-09
- Related ADRs:
  - `docs/adr/0001-artifact-oriented-investigation-workflow.md`
  - `docs/adr/0002-external-step-artifact-submission.md`

## Context

ADR 0001 established a planner-led, artifact-oriented investigation model.

ADR 0002 established the intended direction for external evidence satisfaction:

- `investigation_service` remains the product-owned planner/reconciler
- evidence-plane execution should move toward typed external step-artifact submission
- lower-level handoff tools are valuable seams, but prompt-owned orchestration is too brittle to remain the primary happy path

That architectural gap is now materially clearer.

The codebase already has the important product-owned semantics:

- `planner.py`
  - target resolution
  - plan construction
  - active-batch selection
  - submission validation
  - batch advancement
- `reporting.py`
  - execution context
  - handoff/token wrappers
  - final report rendering
- `models.py`
  - `ActiveEvidenceBatchContract`
  - `EvidenceStepContract`
  - `SubmittedStepArtifact`
  - `ReportingExecutionContext`

The remaining weakness is not semantic modeling.

The remaining weakness is the runtime loop:

- inspect active batch
- run required external-preferred evidence steps
- materialize non-empty `submitted_steps`
- advance one bounded batch
- repeat only as needed
- render late

When that loop lives in prompt text, behavior is intermittent.

The orchestration-core-first path now proved that this loop can move into code and immediately improve the live declarative path. That means the next durable architecture question is no longer "should prompt text own runtime flow?" It should not.

The real open question is:

> What should own execution control, retries, persistence, and resumability once the orchestration core is code-owned?

## Decision

The future direction is:

> Use LangGraph as the execution shell around a product-owned orchestration core, while keeping `investigation_service` as the semantic owner of investigation.

This means:

- `investigation_service` remains the authoritative semantic/control-plane library
- `investigation_orchestrator` becomes the deterministic runtime layer
- LangGraph is the future execution framework for that orchestrator
- BYO LangGraph hosting through kagent is the preferred future deployment direction if hosted resumable execution proves worth the added operational complexity
- that BYO move is a later deployment step, not the first architectural step

This is intentionally a two-stage flexibility model:

- near-term:
  - reduce flexibility in the core happy path by moving orchestration into deterministic code
  - do not return to prompt-owned raw MCP choreography for routine investigations
- later:
  - reintroduce richer execution flexibility through the LangGraph shell itself
  - use graph nodes, bounded branches, retries, resume, and checkpointed state transitions as the controlled place where the runtime becomes more capable again

So the expected place where the system becomes more flexible is not the prompt/tool surface. It is the future execution shell.

LangGraph is not intended to become:

- the owner of plan semantics
- the owner of target semantics
- the owner of reconciliation rules
- the source of truth for investigation domain state

Instead, LangGraph should provide:

- explicit node/edge control
- checkpointed runtime progress
- resumability
- replay/debuggability
- bounded retry/fallback behavior
- richer controlled branching than the current bounded hand-written loop
- a clean future path to BYO hosted execution

This is the main future capability gain beyond the orchestration-core-first merge:

- today, the orchestrator is intentionally narrow and mostly linear
- later, the LangGraph shell is expected to become the place where more flexible investigation control lives
  - continue or stop based on richer state
  - resume a partially completed run
  - branch to follow-up evidence batches with checkpointed state
  - retry or degrade gracefully without returning to agent-improvised raw tool choreography

## Intended Future Flexibility

The desired future flexibility is:

- graph-guided and state-aware
- bounded by code-owned node and edge definitions
- resumable through checkpointed execution state
- capable of retries, fallback branches, and follow-up evidence passes

The desired future flexibility is **not**:

- reopening routine investigation flow to free-form raw MCP choreography in prompts
- asking the model to re-materialize typed submission payloads ad hoc
- treating graph state as a second semantic model beside `ReportingExecutionContext`

Representative examples of the flexibility this ADR is working toward:

1. **Conditional follow-up evidence**
   - if workload evidence is weak but service context exists, branch to a service-evidence node
   - otherwise stop and render from the current reconciled state

2. **Bounded retry and degrade**
   - if peer workload acquisition fails once, retry through a bounded edge
   - if it still fails, record a blocked/fallback state and continue to a partial render instead of reopening prompt improvisation

3. **Resume after interruption**
   - if an investigation pauses after one batch, resume from the checkpointed node with the same `ReportingExecutionContext`
   - do not rebuild the workflow from scratch or rely on the model to remember the prior handoff state

4. **Follow-up investigations from prior state**
   - if a user asks a follow-up question on an already running or recently completed investigation, re-enter from a graph node that already has the reconciled state and remaining budget
   - do not force a completely fresh investigation unless policy requires it

So the intended future flexibility is:

- more branching and recovery at the execution-shell layer
- not more freedom for the prompt layer to improvise the core runtime loop

## What Stays Product-Owned

The following remain in `investigation_service`:

- normalization and target resolution
- investigation subject interpretation
- canonical target selection
- execution-target derivation
- plan construction
- active-batch selection
- submission validation
- artifact reconciliation
- route satisfaction and provenance normalization
- hypothesis ranking
- final report rendering

This ADR does not move those semantics into LangGraph nodes.

## What The Orchestration Layer Owns

The sibling orchestration package owns only runtime control flow:

- seed execution context
- fetch the current active batch
- choose the explicit step runner for external-preferred steps
- materialize `SubmittedStepArtifact`
- advance one bounded batch
- stop or continue based on planner-owned state
- render late

The authoritative runtime snapshot remains `ReportingExecutionContext`.

Any graph-local state should be a thin wrapper around that domain snapshot, not a second semantic model.

## Immediate Follow-On After The Orchestration-Core-First Merge

The orchestration-core-first path does not complete the evidence-plane transition by itself.

In its first practical form, the orchestrator may still use transitional internal evidence helpers in order to prove that:

- the runtime loop is code-owned
- the happy path is deterministic
- the current declarative agent no longer has to materialize `submitted_steps` itself

That is acceptable only as a transitional step.

The next intended migration after the orchestration-core-first merge is:

- keep `run_orchestrated_investigation` or its equivalent high-level runtime path
- keep `investigation_service` as the planner/reconciler and semantic owner
- keep peer evidence planes (`kubernetes-mcp-server`, `prometheus-mcp-server`)
- replace internal evidence acquisition inside `investigation_orchestrator.evidence_runner` with orchestrator-owned peer MCP client calls

This means the future orchestrator should:

- consume planner-emitted execution inputs
- call peer MCP tools programmatically
- materialize `SubmittedStepArtifact` in product-owned code
- submit those artifacts back into planner reconciliation

It does **not** mean:

- removing the peer MCP servers
- returning to prompt-owned tool choreography
- moving planner semantics into the orchestrator or LangGraph

The long-term target is:

- peer evidence planes remain first-class
- product reconciliation remains first-class
- the orchestrator becomes the primary code-owned consumer of peer MCP tools in the normal happy path

## Intended LangGraph Shape

The future LangGraph graph should be small and mostly deterministic.

A representative flow is:

1. seed context
2. get active batch
3. if no active batch remains, render
4. run required external-preferred step runners
5. advance one bounded batch
6. if another batch remains and budget allows, loop
7. render

This is intentionally not an LLM-driven graph.

The graph exists to provide:

- explicit state transitions
- checkpointing
- bounded looping
- retry/fallback control
- replay/resume

It does not exist to redefine investigation semantics.

## Persistence Direction

The orchestration-core-first path does not require durable persistence.

However, a future fully realized LangGraph runtime should support resumable and recoverable investigations.

That requires storing execution state somewhere.

The intended default future path is:

- use kagent-backed checkpointing for LangGraph execution state first
- do not require an external Redis/Postgres dependency by default just to begin using the LangGraph execution shell

This aligns with the documented kagent BYO LangGraph direction, where LangGraph state is stored through kagent rather than through a separately provisioned checkpoint store in the initial example.

External checkpoint stores remain optional later if:

- kagent-backed persistence proves insufficient
- custom durability or retention requirements emerge
- multi-system execution coordination requires a separate store

So the rule for now is:

- near-term orchestration core: no durability required
- future checkpointed LangGraph shell: prefer kagent-backed checkpointing first

## Deployment Sequencing

This ADR does not prescribe an immediate switch to a BYO LangGraph agent.

The intended rollout order is:

1. keep the current declarative agent path
2. move orchestration into code through a high-level product-owned runtime path
3. prove live parity on current validations
4. add a true LangGraph shell around the orchestration layer
5. if hosted execution, resumability, or runtime separation become active requirements, package that LangGraph runtime as a shadow BYO agent through kagent
6. only after shadow validation, consider making the BYO LangGraph agent the preferred runtime path

This avoids changing:

- agent type
- image build path
- persistence model
- orchestration logic

all at once.

## Future Deployment Direction

The architectural decision in this ADR is about the execution shell, not immediate hosting.

However, the expected later operational landing zone is:

- keep the same semantic/control-plane ownership in `investigation_service`
- keep peer MCP servers as first-class evidence planes
- host the LangGraph-backed orchestration runtime as a BYO kagent agent if and when:
  - resumable execution is required
  - hosted runtime separation is worth the operational cost
  - shadow/canary comparison against the declarative agent is complete

This means a future BYO LangGraph agent would most likely:

- reuse the same orchestration core
- continue talking to the same MCP servers
- reduce the need for a large declarative investigation tool surface
- change the outer hosting/runtime model more than the underlying server topology

So the intended long-term direction is:

- orchestration core first
- transport migration next
- BYO LangGraph hosting later if it proves beneficial

This ADR does not require that deployment move now, but it does treat it as the preferred future option if the operational trade-off is justified.

## Consequences

### Positive

- runtime orchestration is no longer prompt-owned
- investigation semantics remain centralized in product code
- future resumability and replay have a clear architectural home
- a later BYO LangGraph deployment can reuse the same orchestration core
- current declarative deployments can improve before any hosting cutover

### Negative

- there is now one more architectural layer to maintain:
  - semantic core
  - orchestration layer
  - eventual execution shell
- some transitional duplication may remain while low-level handoff tools still coexist with the new high-level path
- full resumability is still deferred until a real LangGraph shell and checkpointing are added

## What This ADR Does Not Decide

This ADR does not decide:

- whether to merge any specific PR
- whether BYO ADK should still be explored as an outer hosting model
- whether peer MCP clients should immediately replace all internal evidence helpers in the first orchestration phase
- when the broader subject/target model should be fully generalized

Those remain follow-on decisions.

The narrower decision here is:

> The future LangGraph path should be execution-shell-first, not semantic-owner-first.
