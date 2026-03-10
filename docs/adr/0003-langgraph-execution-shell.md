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

## First Implementation Slice

The first implementation of this ADR is intentionally narrow:

- keep `run_orchestrated_investigation(...)` as the public facade
- compile a small in-process LangGraph graph inside `investigation_orchestrator`
- map the current handwritten loop to explicit graph nodes over existing seams:
  - `ensure_context`
  - `load_active_batch`
  - `run_external_steps`
  - `advance_batch`
  - `render_report`
- keep `ReportingExecutionContext` as the only authoritative runtime snapshot inside graph state
- keep `_maybe_attach_resolved_pod_context(...)` as post-graph compatibility behavior

This first slice does not:

- change the user-facing tool or route surface
- add a public resume API
- replace handoff tokens
- introduce hosted LangGraph execution
- move planner or reporting semantics out of `investigation_service`

Checkpointing is introduced only as an execution-shell hook:

- compile the graph with an optional checkpointer
- pass `thread_id` and optional `checkpoint_id` through graph invoke config
- use in-process checkpointing first for local proving and tests

## Current Status After The First Shell And Peer-Transport Slices

The repo has now completed the intended first shell slice and the peer evidence-plane transport migration that was meant to follow it.

What is now true:

- `run_orchestrated_investigation(...)` is the stable public facade and uses the in-process LangGraph shell internally
- graph state remains a thin wrapper around `ReportingExecutionContext`
- checkpoint configuration is behind an orchestrator-side seam and is exercised in tests with in-memory checkpointing
- the orchestrator now has an explicit internal thread identity seam for checkpointed graph execution
- checkpoint-resume behavior is exercised in tests from real graph-node boundaries rather than only through terminal state inspection
- minimal redacted runtime observability exists around graph run start/finish, node transitions, and checkpoint interruption/resume boundaries
- workload, service, and node external-preferred evidence steps now use orchestrator-owned peer MCP transport first
- bounded internal fallback remains planner-owned inside product code rather than living in the orchestrator happy path

What is still intentionally deferred:

- public resume APIs
- durable checkpoint storage by default
- caller-facing thread identity
- a hosted BYO LangGraph runtime

So the architecture is now at the point where a future BYO LangGraph move should be treated as a hosting/runtime packaging step, not as a semantic rewrite.

## Current Status After The Local Shadow BYO Slice

The repo has now completed the first additive local shadow-hosting slice for the BYO direction.

What is now true:

- a separate `investigation_shadow_runtime` package exists as an outer host layer
- that host calls the orchestrator library directly rather than routing back through `investigation_service.mcp_server`
- a small stable host-facing orchestrator API now exists so the host does not depend on private `_run_*` helpers or exception-only interruption detection
- local kind packaging now supports a separate shadow BYO agent beside the current declarative `incident-triage` path
- local shadow validation is now repeatable on a warm kind cluster and includes output-quality assertions rather than only health checks
- deterministic host-side formatting is now applied from `InvestigationReport` so the shadow lane can be compared side by side with the declarative lane

What is intentionally true only for local validation right now:

- the local shadow lane uses `SHADOW_CHECKPOINT_MODE=memory`
- local kind validation proves the hosted packaging boundary and direct-orchestrator call path
- local kind validation does not yet prove the real kagent-backed checkpoint path

What remains to close before treating the BYO path as more than a local shadow proof:

- validate the shadow lane in homelab/GitOps as a separate hosted runtime
- choose and document the hosted thread identity boundary explicitly
- exercise real kagent-backed checkpoint storage in the outer host layer
- strengthen side-by-side hosted observability so invoke/resume behavior is inspectable without guesswork

## Current Status After Initial Homelab Shadow Validation

The repo has now crossed the next important threshold after the local shadow proof:

- the shadow BYO lane has completed real homelab invocations through Claude Code and the kagent controller MCP path
- the vague unhealthy-pod workload path is now operationally credible in hosted form
- the shadow lane is still running with `SHADOW_CHECKPOINT_MODE=memory`
- the hosted kagent-backed checkpoint path remains a separate unresolved integration problem

The most important new result is that the next weakness is no longer hosting for the workload path.
The next weakness is evidence quality parity across investigation modes.

What the current hosted validation proves:

- workload investigation through the shadow lane can resolve a vague pod target and produce a structured five-section answer
- the hosted shadow lane is now comparable to the declarative lane for the simple crashloop workload case

What the current hosted validation also proves:

- service-alert investigation is materially weaker than workload investigation
- the system can produce a structurally correct answer for a service alert while still being semantically weak
- shadow-hosting success must not be mistaken for service-evidence quality success

## Service-Alert Weakness And Desired State

The current weak point is not that the system cannot host the shadow lane.
The weak point is that service-alert investigation depends on a weaker evidence pipeline than the workload path.

Today the service-alert path still relies on a fragile combination of:

- alert normalization that must derive a useful service investigation target from alert-shaped text
- service-level Prometheus evidence whose metric names and label assumptions may not match real homelab telemetry consistently
- Kubernetes fallback that may fail to infer useful deployment, selector, or pod context from the service-oriented inputs
- final rendering that can degrade to "service signals inconclusive" even when hosting and control-plane orchestration are functioning correctly

That is why the crashloop workload case can work well while a real `EnvoyHighErrorRate` case remains weak.
These are different evidence paths with different maturity levels.

The desired state is:

- alert normalization preserves the original alert facts while producing an explicit execution-facing service target
- service investigations always attempt Prometheus evidence using label and metric conventions that are validated against the real cluster
- when service metrics are weak or absent, Kubernetes fallback still returns useful service-to-workload context rather than only generic failure notes
- service-alert reports remain honest about ambiguity, but they should fail "informatively" rather than collapsing to a near-empty evidence set
- hosted shadow and declarative lanes should be judged on the same semantic quality bar for:
  - vague workload prompts
  - alert-shaped workload prompts
  - service alerts
  - node alerts

The architectural implication is:

- shadow hosting should now be treated as sufficiently proven for workload parity exploration
- the next product-quality slice should focus on service-alert normalization and service-evidence collection correctness
- promotion beyond shadow should remain blocked until service and alert quality are closer to workload quality

## Readiness Gates Before Any BYO Shadow Agent

Before introducing a shadow BYO LangGraph agent through kagent, the following should be true:

1. the LangGraph shell is already the only real runtime behind `run_orchestrated_investigation(...)`
2. `ReportingExecutionContext` remains the only authoritative domain snapshot in graph state
3. peer evidence transport runs through orchestrator-owned MCP clients rather than declarative tool injection
4. checkpointing remains abstracted behind a repo seam rather than being wired directly into product semantics
5. deterministic report formatting is available directly from `InvestigationReport`

The remaining practical gates before a BYO shadow-hosting slice are:

- choose the caller-facing thread identity strategy at the hosted runtime boundary
- keep public resume APIs deferred until that hosting boundary exists
- package the BYO runtime as a separate outer hosting layer rather than folding kagent-specific code into `investigation_orchestrator`

Until those gates are closed, the repo should be treated as:

- architecturally ready for BYO shadow-hosting preparation
- not yet fully ready for a production-worthy BYO shadow rollout until the hosted thread identity boundary is explicit

Those original gates are now satisfied for a local shadow-hosting proof.

The next practical gates after this local slice are:

- prove the same shadow runtime shape in homelab/GitOps
- validate real hosted checkpoint behavior there rather than only in-memory local checkpoints
- keep public resume APIs deferred until the hosted thread identity strategy is explicit and stable

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
- first LangGraph shell slice: optional in-process checkpointing hooks only
- future checkpointed LangGraph shell: prefer kagent-backed checkpointing first

## Deployment Sequencing

This ADR does not prescribe an immediate switch to a BYO LangGraph agent.

The intended rollout order is:

1. keep the current declarative agent path
2. move orchestration into code through a high-level product-owned runtime path
3. prove live parity on current validations
4. add a true in-process LangGraph shell around the orchestration layer with optional checkpoint hooks
5. prove graph parity before any public resume or hosting changes
6. if hosted execution, resumability, or runtime separation become active requirements, package that LangGraph runtime as a shadow BYO agent through kagent
7. only after shadow validation, consider making the BYO LangGraph agent the preferred runtime path

This avoids changing:

- agent type
- image build path
- persistence model
- orchestration logic

all at once.

## After Shadow Validation

If the shadow BYO LangGraph agent proves operationally worthwhile, the expected next move is:

- promote the BYO LangGraph agent to the preferred hosted runtime path
- keep `investigation_service` as the semantic/control-plane owner
- keep `investigation_orchestrator` as the reusable execution engine
- keep peer MCP servers as the evidence-plane transport boundary

That promotion should mean:

- the same orchestration core is reused
- the main change is the outer hosting/runtime model
- resumable execution and checkpoint-backed recovery become normal runtime capabilities of the preferred path

It should **not** mean:

- moving investigation semantics into the hosted agent wrapper
- reopening prompt-owned MCP choreography as the routine happy path
- replacing peer MCP servers with direct ad hoc evidence collection in prompts

The most likely steady-state outcome after successful shadow validation is:

- BYO LangGraph becomes the preferred hosted runtime
- the declarative agent path is retained temporarily as a comparison, fallback, or rollback lane
- public resume or session-facing APIs are considered only after the hosted thread identity model is stable and proven useful

The precise retirement/deprecation timing for the declarative runtime remains a follow-on decision, but the intended direction is:

- shadow first
- preferred-runtime promotion second
- declarative-path demotion or retirement only after hosted parity, observability, and rollback confidence are strong enough

After the local shadow slice, "shadow first" should now be read concretely as:

- local kind proof first: completed
- homelab/GitOps side-by-side hosted proof second: next
- preferred-runtime promotion only after hosted checkpoint behavior, observability, and rollback confidence are good enough

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
