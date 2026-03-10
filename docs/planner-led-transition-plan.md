# Planner-Led Transition Plan

- Status: Draft
- Date: 2026-03-09
- Related ADR: `docs/adr/0001-artifact-oriented-investigation-workflow.md`

## Purpose

This document turns ADR 0001 into an execution plan.

The goal is not another broad redesign. The goal is to move the current system from:

- report-first agent behavior
- mostly implicit planning
- staged internals hidden behind report facades

to:

- planner-led agent behavior
- explicit investigation planning and re-planning
- direct evidence-plane exploration
- product-owned investigation semantics

while preserving:

- one user-facing read-only triage agent
- one `investigation-mcp-server` deployment
- compatibility only where it still earns its keep during transition

## Current State

The codebase now has the core nouns and seams needed for a real planner-led system:

- `InvestigationTarget`
- `EvidenceBundle`
- `Hypothesis`
- `InvestigationAnalysis`
- `InvestigationPlan`
- `PlanStep`
- `EvidenceBatch`
- `EvidenceBatchExecution`
- `StepArtifact`

The architecture is now beyond a fake-plan stage, but it is still transitional.

What is now true:

- planning is explicit and separate from evidence collection
- one bounded evidence batch can be executed at a time
- plan state can be updated after a batch executes
- report paths now use the first executed batch instead of bypassing the plan entirely
- executed change artifacts can be reused during final rendering

What is still missing:

- a real alert-plane artifact instead of alert-shaped runtime indirection
- stronger real-cluster validation around alert and init-container cases
- clearer observability of which peer tool surfaces the agent actually chose during a run

## Post-Slice-7 Validation Findings

Recent homelab validation changed the shape of the remaining work:

- the planner-led path is now working against a real cluster, not just kind
- local/default cluster access now falls back cleanly to in-cluster auth when no multicluster kubeconfig is mounted
- the remaining report-first and context-shaped backend surfaces have been removed
- observability now shows that peer tool servers can satisfy an investigation without necessarily using the planner/control-plane path from `investigation-mcp-server`

What this means:

- the next problem is no longer legacy surface cleanup
- the next problem is making real-cluster behavior correct and making tool-path choice observable enough to judge whether the planner-led path is being preferred appropriately

## Planned Surface Retirement

The transition plan should explicitly track which surfaces are being carried temporarily and which ones are intended to disappear.

### Remove From The Agent-Visible Surface

These should stop being part of the intentional kagent tool vocabulary as Slice 5 and Slice 6 land:

- `build_investigation_report`
- `build_alert_investigation_report`
- `collect_workload_context`
- `collect_service_context`
- `collect_node_context`
- `collect_alert_context`
- likely `normalize_alert_input`
- likely `normalize_incident_input`

These may remain reachable at the MCP or HTTP layer briefly during transition, but they should not be taught as canonical tools once the planner-led path is the intended runtime model.

### Remove From The Backend Entirely

These should be scheduled for full removal once the kagent config has switched and no remaining tests, routes, or callers depend on them:

- `build_investigation_report`
- `build_alert_investigation_report`
- `collect_workload_context`
- `collect_service_context`
- `collect_node_context`
- `collect_alert_context`

These removals are not optional cleanup. They are part of finishing the planner-led transition and avoiding indefinite maintenance of two parallel mental models.

### Already Retired From The Public Surface

These have already been removed from the public FastAPI and MCP surface and should stay retired as first-class entrypoints:

- `build_root_cause_report`
- `collect_correlated_changes`

## Current Direction

The current recommended direction is now:

- merge the orchestration-core-first path into the current declarative runtime
- keep `investigation_service` as the semantic/control-plane core
- keep lower-level handoff tools available for debugging and transition
- treat a future LangGraph rollout as an execution-shell phase, not as a semantic rewrite

This means the near-term product goal is:

- deterministic code-owned orchestration now

not:

- immediate BYO agent replacement
- immediate LangGraph checkpointing
- immediate persistence or resumability work

The future LangGraph-specific direction is documented separately in:

- `docs/adr/0003-langgraph-execution-shell.md`

That future direction is:

- `investigation_orchestrator` becomes the runtime layer
- LangGraph becomes the future execution shell
- kagent-backed checkpointing is the default first persistence path if/when resumable execution is introduced
- a future BYO LangGraph agent is the preferred later deployment direction if hosted execution/resume value justifies the added operational complexity

The most important follow-on after the orchestration-core-first merge is now explicit:

- keep `run_orchestrated_investigation` as the preferred high-level path
- keep `investigation_service` as the planner/reconciler
- keep peer MCP servers and their tool catalogs
- replace transitional internal evidence collection inside `investigation_orchestrator.evidence_runner` with orchestrator-owned peer MCP client calls

That means the next transport-oriented migration is:

- not removing `kubernetes-mcp-server` or `prometheus-mcp-server`
- not asking the agent to manually choreograph those peer tools again
- but shifting primary consumption of those peer MCP tools from prompt-driven agent behavior to orchestrator-owned code

The future BYO question is intentionally sequenced after those steps:

- first fix orchestration in the current declarative path
- then move evidence transport toward orchestrator-owned peer MCP execution
- then decide whether the hosted runtime should stay declarative-plus-MCP or move to a shadow BYO LangGraph agent

So the current plan does not require an immediate BYO move, but it does keep that as the preferred later deployment direction once the orchestration core and transport split are stable.

### Ordered Follow-On After The Orchestration-Core-First Merge

The intended sequence after the orchestration-core-first merge is:

1. workload peer-MCP transport
   - replace transitional internal workload evidence collection in `investigation_orchestrator.evidence_runner`
   - keep `run_orchestrated_investigation` unchanged as the preferred high-level path
   - prove that workload evidence is gathered through orchestrator-owned Kubernetes MCP calls rather than prompt choreography or internal helper transport

2. service peer-MCP transport
   - move service evidence acquisition to orchestrator-owned Prometheus MCP calls first
   - keep Kubernetes enrichment or fallback explicit where still needed

3. node peer-MCP transport
   - apply the same transport split to node evidence
   - keep planner/reconciler semantics unchanged

4. runtime seam cleanup
   - tighten `runtime_api.py`
   - refine the product-owned submission/materialization seam
   - reduce dependence on transitional helper coupling

5. low-level tool transition cleanup
   - keep low-level handoff tools available for debugging as needed
   - clarify their longer-term support/deprecation status once the high-level path is stable on peer MCP transport

6. real LangGraph execution shell
   - compile the orchestration core into a true LangGraph runtime
   - add checkpointing/resume support
   - keep `investigation_service` as the semantic owner

7. optional shadow BYO LangGraph agent
   - package the LangGraph-backed runtime as a BYO agent only after orchestration and transport are stable
   - compare it side by side with the declarative path before deciding on any hosting cutover

## Completed Slices

### Slice 1: Add Explicit Planning Artifacts

Status: Completed

Delivered:

- `InvestigationPlan`
- `PlanStep`
- `PlanStatus`
- `EvidenceBatch`
- mode-aware planning:
  - `alert_rca`
  - `targeted_rca`
  - `factual_analysis`
- `build_investigation_plan`
- report paths refactored to plan first and render late
- `render_investigation_report` established as the canonical final-stage renderer

Validation delivered:

- unit tests for mode-aware plan construction
- plan shape tests for alert, targeted, and factual questions
- reporting tests proving planning no longer collects evidence

### Slice 2: Add Bounded Iterative Plan Execution

Status: Completed

Delivered:

- `active_batch_id` on `InvestigationPlan`
- explicit execution artifacts:
  - `EvidenceBatchExecution`
  - `StepArtifact`
- new control-plane entrypoints:
  - `execute_investigation_step`
  - `update_investigation_plan`
- closed planner-owned execution dispatch keyed by step ids
- one-batch-at-a-time execution
- conservative plan update behavior after each batch
- one bounded service follow-up branch before analysis when initial workload evidence is inconclusive
- report paths refactored to:
  - build a plan
  - execute the first bounded evidence batch
  - update the plan
  - analyze the primary evidence artifact
  - render
- FastAPI and MCP exposure for the new control-plane tools

Guardrails preserved:

- no arbitrary autonomous loops
- no write actions
- no generic raw-tool executor
- no multi-agent behavior

Validation delivered:

- planner tests covering:
  - batch execution
  - plan update after execution
  - bounded follow-up insertion
  - factual-mode execution rejection
- reporting tests proving render paths now use executed artifacts
- contract tests for the new public control-plane routes

## Recommended Next Slices

### Slice 3: Make Investigation State Canonical

Status: Completed

Delivered:

- `InvestigationState` added as the canonical post-execution artifact
- state assembly moved into a dedicated `state.py` module
- execution-time target alignment moved out of `planner.py`
- `rank_hypotheses` now delegates to `rank_hypotheses_from_state(...)`
- `render_investigation_report` now delegates to `render_investigation_report_from_state(...)`
- request-based reporting entrypoints remain as thin facades over the canonical state path
- the internal legacy root-cause bridge was removed from canonical reporting
- compatibility helpers now sit on top of state-native ranking/rendering rather than carrying unique reasoning logic

Validation delivered:

- service tests proving ranking/rendering consume canonical state
- planner tests still proving plan creation is pure and execution remains bounded
- reporting tests proving executed change artifacts still flow through the canonical state path

What is still missing after Slice 3:

- a real alert-plane artifact rather than alert-shaped runtime indirection
- broader public-surface cleanup of redundant transition endpoints and tool names
- agent/runtime transition to intentionally planner-led behavior

### Slice 4: Narrow and Clean the Public Surface

Status: Completed

Delivered:

- removed the public FastAPI route for `build_root_cause_report`
- removed the public FastAPI route for `collect_correlated_changes`
- removed the corresponding MCP tools from `mcp_server.py`
- retained internal Python helpers temporarily where they still support tests or compatibility code paths
- clarified MCP descriptions so context tools are explicitly exploratory rather than planner-led core
- kept `collect_change_candidates` as the canonical staged change-review surface

What was intentionally left out of this slice:

- no removal of `build_investigation_report` or `build_alert_investigation_report` yet
- no removal of `collect_*_context` routes/tools yet
- no alert-plane artifact redesign
- no agent prompt/config change yet

Validation gate:

- route/MCP tests proving canonical tools behave as described
- tests proving removed or demoted surfaces no longer carry unique logic

### Slice 5: Introduce a Narrow Evidence-Plane Policy for the Agent

Status: Completed

Delivered:

- narrowed the kagent-visible `investigation-mcp-server` tool catalog in `k8s/agent.yaml`
- removed stale references to already-retired public tools from the agent allowlist
- added `agent.yaml` to the `k8s/` kustomization so the agent manifest is deployed from the same manifest set
- updated the kagent skill ConfigMap so it no longer teaches report-first wrappers or hidden exploratory tools as the default path
- kept compatibility and exploratory tools exported in MCP/HTTP for transition and debugging, but removed them from the intentional agent-visible catalog
- tightened MCP tool descriptions so compatibility and exploratory surfaces are called out more explicitly
- updated local agent-facing wrappers, docs, and validation scripts to stop teaching `build_*report` as the default agent path
- added policy tests to lock the agent allowlist, prompt guidance, and manifest-to-MCP consistency in place

The intentional kagent-visible set is now:

- control-plane:
  - `resolve_primary_target`
  - `build_investigation_plan`
  - `execute_investigation_step`
  - `update_investigation_plan`
  - `rank_hypotheses`
  - `render_investigation_report`
- product-owned evidence-plane:
  - `collect_change_candidates`
- peer evidence-plane MCP servers:
  - read-only Kubernetes MCP for direct runtime inspection:
    - resource lookup
    - pod logs
    - events
    - namespace-scoped runtime evidence
  - Prometheus MCP for metrics and alert evidence:
    - targeted PromQL
    - alert lookup
    - rules
    - targets
    - exemplars

Transitional internals that may remain behind the backend for now, but are no longer the intentional agent-visible contract:

- `normalize_incident_input`
- `collect_alert_evidence`
- `collect_workload_evidence`
- `collect_service_evidence`
- `collect_node_evidence`

Validation gate:

- agent routing tests for prompt/tool-choice behavior
- assertions on first tool and follow-up tool sequences
- kagent-visible tool catalog excludes:
  - `build_investigation_report`
  - `build_alert_investigation_report`
  - `collect_*_context`
  - retired aliases that no longer represent the planner-led model

### Slice 6: Transition the kagent Skill Config from Report-First to Planner-Led

Status: Completed

Delivered:

- rewrote the local kagent skill ConfigMap to teach an explicit planner-led sequence:
  - resolve when needed
  - build plan
  - execute one bounded evidence batch
  - update plan
  - optionally execute one bounded follow-up batch
  - render late
- rewrote local Claude command wrappers, plugin command wrappers, and the Claude skill wrapper to use the same planner-led sequence instead of report-first directives
- updated the desktop extension wrapper generator and bundled metadata to describe the planner-led path instead of `build_*report` entrypoints
- updated local docs and demo text so agent-facing examples no longer teach report-first behavior
- reworked the alert validation lane so it exercises the agent path with an alert-shaped planner-led prompt instead of directly calling a compatibility HTTP route
- strengthened policy coverage so planner-led sequencing language is asserted across the local skill prompt and wrapper surfaces

Prompt behavior now is:

- plan first
- gather bounded evidence batches
- update plan
- synthesize late
- use `rank_hypotheses` only when explicit ranked-cause analysis is needed rather than as a mandatory pre-render step

Validation gate:

- policy tests asserting planner-led prompt and wrapper language
- syntax checks for updated validation lanes and desktop wrapper generator
- full local test suite

### Slice 7: Remove Transitional Report-First And Context Surfaces

Status: Completed

Delivered:

- removed `build_investigation_report` from the backend and public surfaces
- removed `build_alert_investigation_report` from the backend and public surfaces
- removed `collect_workload_context` from the backend and public surfaces
- removed `collect_service_context` from the backend and public surfaces
- removed `collect_node_context` from the backend and public surfaces
- removed `collect_alert_context` from the backend and public surfaces
- rewired `/investigate` and the remaining contract tests to the canonical `render_investigation_report` path
- simplified planner dependencies so planning no longer carries dead context-collection seams

Validation delivered:

- route and MCP tests proving the removed surfaces are gone
- service tests proving canonical ranking and rendering no longer depend on the deleted entrypoints
- full local suite green after the deletion pass

### Slice 8: Real-Cluster Correctness And Planner-Path Preference

Status: In Progress

Delivered so far:

- corrected a real homelab workload diagnosis gap for init-blocked pods:
  - init-container state is now collected and analyzed explicitly
  - `PodInitializing` init-block cases are no longer mislabeled as generic restart failures
  - the validated homelab `toolbridge-api-migrate-j5wwf` case now returns `Init Container Dependency Blocked`
- strengthened alert-path correctness:
  - alert normalization no longer promotes workload alerts to `service` simply because a service label is present
  - alert summaries now preserve both the original alert-derived target and the resolved runtime target
- added a minimal backend-owned planner trace to the canonical state/report path
- extended local and homelab validation so planner-led real-cluster behavior is being checked against actual runtime cases, not only kind fixtures

What homelab validation now proves:

- the planner-led path is working against a real cluster
- the local/default in-cluster fallback fix is working
- the init-container dependency-block case is materially improved
- peer tool-server usage is part of the runtime story and can influence which surface actually satisfies an investigation

What is still open in Slice 8:

- exact per-tool observability is still incomplete:
  - we can now distinguish controller activity, `kagent-tools` activity, and `investigation-mcp-server` activity
  - we still do not have a clean tool-name ledger for every peer tool call
- some real-cluster cases still stop at a correct high-level diagnosis instead of following dependencies as far as they could
- planner-path preference still needs to be judged intentionally rather than assumed:
  - some runs use peer built-in tools instead of the planner/control-plane path
  - the remaining question is when that is acceptable and when the planner-led path should be preferred
- the local `k8s/` bundle still needs to mirror the newer peer evidence-plane shape from homelab:
  - explicit Kubernetes MCP server
  - explicit Prometheus MCP server
  - narrower `investigation-mcp-server` allowlist
- public MCP and HTTP surfaces still export some planner-owned evidence helpers that are no longer part of the intended agent vocabulary
- local wrappers and policy tests still need to encode the new peer evidence-plane contract more explicitly

### Slice 9: External Evidence Handoff And Reconciliation

Status: In Progress

Goal:

- make the planner-led architecture operationally real by separating:
  - planner/reconciler responsibilities
  - evidence-gatherer responsibilities
  - later adapter-facing outcome packaging

Planner/reconciler responsibilities should remain product-owned:

- resolve target
- build plan
- expose one bounded active evidence batch
- reconcile submitted step artifacts
- update the plan
- rank hypotheses
- render the final report

Delivered so far:

- active evidence batches are now exposed as execution-facing contracts
- externally gathered step artifacts can now be submitted and reconciled into canonical `StepArtifact` and `EvidenceBatchExecution` records
- mixed batches now preserve pending planner-owned steps instead of forcing whole-batch completion
- `execute_investigation_step(...)` now behaves as bounded fallback over the remaining pending steps
- canonical reporting can now consume reconciled executions before falling back to one bounded internal execution when primary evidence is still missing
- a canonical runtime-progress helper can now advance one active batch by reconciling submitted external evidence first and then auto-running only the remaining same-batch planner-owned steps
- the agent-visible allowlist and taught wrappers now include:
  - `get_active_evidence_batch`
  - `submit_evidence_step_artifacts`
  - `advance_investigation_runtime`

Still open in this slice:

- the normal orchestrated runtime still needs to prefer the submission path intentionally rather than treating fallback execution as the easiest default
- live validation now shows the fine-grained handoff tools are still too choreography-heavy to be the only preferred agent-facing happy path
- the agent understands the intended sequence conceptually, but still mis-shapes low-level arguments such as `plan`, `incident`, `submitted_steps`, and `execution_context`
- the next sub-slice should add a higher-level batch handoff helper above the fine-grained primitives while keeping those primitives available for adapters, testing, and debugging
- report/rank flows should grow stronger end-to-end coverage for externally submitted plus planner-owned mixed batches
- target resolution still needs to grow a more explicit subject-to-target-to-execution-target contract rather than relying on the current transitional target model alone

Preferred direction inside the slice:

- teach a higher-level batch handoff helper as the default agent-facing runtime surface
- keep the fine-grained handoff tools available as lower-level seams for adapters, debugging, and explicit choreography

Next follow-on after the orchestration-core-first merge:

- keep the new high-level runtime path
- keep the planner/reconciler semantics unchanged
- migrate `investigation_orchestrator.evidence_runner` away from transitional internal collectors:
  - first workload evidence
  - then service evidence
  - then node evidence
- have the orchestrator satisfy external-preferred evidence by calling peer MCP tools programmatically and then materializing `SubmittedStepArtifact` in product-owned code

This preserves the intended direction from ADR 0002:

- peer evidence planes remain first-class
- product reconciliation remains product-owned
- prompt choreography is not reintroduced

In this slice, target resolution should remain product-owned but should become more operationally explicit.

That means it should continue to preserve:

- the broader investigation subject
- the canonical resolved current investigation target
- the resolved investigation scope

And it should also begin to produce:

- execution-facing target details for each bounded evidence step
- the concrete target inputs an external evidence gatherer needs to satisfy that step without re-owning normalization semantics

This should not assume that every investigation starts from one concrete object reference.

The future shape should support a broader subject such as:

- a single alert
- a group of related alerts
- a service symptom
- an operator-owned convenience object
- a vague unhealthy workload report

From that broader subject, the planner/reconciler should choose a current canonical target and derive bounded execution targets for evidence gathering.

Evidence gatherer responsibilities should move toward external execution:

- satisfy one bounded evidence step or batch
- use Kubernetes MCP for runtime evidence where appropriate
- use Prometheus MCP for metrics evidence where appropriate
- return typed artifacts plus route provenance

Delivered in this slice should be:

- an execution-facing representation of the active evidence batch
- a typed submitted-step-artifact contract
- a reconciliation path that updates plan state from externally satisfied evidence steps
- execution-facing target details attached to active evidence steps so external evidence gathering does not have to reinterpret canonical target semantics on its own
- explicit fallback semantics where `execute_investigation_step(...)` remains available as bounded internal execution during transition
- parity in downstream state/render behavior regardless of whether evidence artifacts were submitted externally or executed internally

Longer term, this slice should set up a clearer distinction between:

- investigation subject
- canonical current investigation target
- execution targets for bounded evidence gathering

What this slice should not do:

- do not add a broad raw-tool orchestration layer
- do not move planning semantics into peer MCP servers
- do not introduce the final adapter-facing `InvestigationOutcome` envelope yet

Validation gate:

- tests proving externally submitted step artifacts advance plan state correctly
- tests proving follow-up insertion and batch progression work from submitted artifacts
- tests proving fallback internal execution and external submission converge on the same artifact semantics
- real-cluster validation that distinguishes preferred peer evidence paths from bounded fallback execution

### Slice 10: Stable Outcome Envelope

Status: Proposed

Goal:

- add a canonical adapter-facing `InvestigationOutcome` only after external evidence submission and reconciliation are part of the normal flow

Delivered in this slice should be:

- `InvestigationOutcome` wrapping reconciled:
  - `InvestigationState`
  - `InvestigationAnalysis`
  - `InvestigationReport`
- honest completion status such as:
  - `completed`
  - `partial`
  - `blocked`
  - `failed`
- a compact execution summary derived from reconciled submitted artifacts and bounded fallback execution

This slice should remain:

- adapter-facing
- trigger-agnostic
- downstream of planner/reconciler and evidence-gatherer handoff

Validation gate:

- tests proving outcome status reflects reconciled execution truth
- tests proving outcome packaging is stable across externally submitted and fallback-executed evidence paths
- integration checks for future adapter consumers without widening the agent-visible tool vocabulary

### Next Cleanup After Peer Evidence-Plane Adoption

Once the peer evidence-plane servers are the normal path, the next cleanup should intentionally remove or demote the remaining transitional public surfaces that overlap with them.

Remove from the intentional public/agent contract:

- `normalize_incident_input`
- `collect_workload_evidence`
- `collect_service_evidence`
- `collect_node_evidence`
- `collect_alert_evidence`

Planned replacements:

- Kubernetes MCP replaces direct runtime inspection helpers:
  - logs
  - events
  - pod and workload inspection
  - namespace-scoped Kubernetes lookup
- Prometheus MCP replaces direct metrics and alert evidence helpers:
  - targeted PromQL
  - alert and rule lookup
  - targets
  - exemplars
- planner-led prompt and control-plane tools remain responsible for:
  - target resolution
  - plan construction
  - bounded execution sequencing
  - hypothesis ranking
  - final report rendering

This keeps the product semantics in `investigation-poc` while removing duplicated public evidence helpers once equivalent peer evidence planes are available and stable.

Validation gate:

- homelab investigations for real pods and services
- corrected diagnosis for known real-cluster cases that previously returned misleading interpretations
- visible enough proof of peer tool-surface usage to distinguish:
  - `investigation-mcp-server`
  - `kagent-tools`
  - controller-only agent invocation

## Immediate Recommendation

The next implementation move should be:

### Continue Slice 8, Then Finish Slice 9 Adoption

Why:

- the deletion pass is complete, so the remaining risk is behavior quality rather than surface sprawl
- real-cluster diagnosis quality has already improved, but the slice is not complete yet
- observability now shows that peer tool usage is part of the runtime story and still needs better attribution
- once Slice 8 observability is good enough, the next highest-leverage move is explicit external evidence handoff, reconciliation, runtime adoption of `advance_investigation_runtime`, and then a higher-level batch handoff helper for agent reliability

Implementation order after current Slice 8 work:

1. expose active evidence batches as execution-facing contracts
2. add typed submitted-step-artifact reconciliation
3. add the canonical runtime-progress helper and keep `execute_investigation_step(...)` as bounded fallback during transition
4. keep the fine-grained primitives available for adapters, testing, and debugging:
   - `get_active_evidence_batch`
   - `submit_evidence_step_artifacts`
   - `advance_investigation_runtime`
5. add a higher-level batch handoff helper for the preferred agent-facing runtime path so the model does not have to reconstruct low-level orchestration repeatedly
6. add `InvestigationOutcome` only after reconciled execution becomes canonical

As part of Slice 9, make target resolution intentionally feed execution-facing target details into the active evidence batch contract rather than remaining only an internal normalization step.

What not to do yet:

- do not introduce a generic raw-tool orchestration layer
- do not add more compatibility surfaces back into the backend
- do not assume peer evidence-tool usage is bad by default; measure and judge it first
- do not introduce `InvestigationOutcome` ahead of the evidence submission boundary

## End-State E2E Expectations

When the target model is in place, e2e coverage should include:

- alert investigation:
  - explicit alert-plane facts
  - plan creation
  - changes
  - target localization
  - hypothesis ranking
  - final report
- targeted workload investigation:
  - explicit target resolution
  - bounded evidence batches
  - final report
- operator-backed target investigation:
  - convenience target preservation
  - resolved operational target
  - final rendered answer keeps operator context
- factual/capacity analysis:
  - no forced RCA report-first behavior
  - evidence collection and direct summary
- ambiguity handling:
  - close hypotheses soften confidence
  - ambiguity limitation appears
  - follow-up reflects alternative plausible cause
- agent behavior:
  - tool sequence is observable and asserted
  - planner-led path is used intentionally

## Anti-Patterns to Avoid

- keeping ranking and rendering request-centric after execution exists
- exposing overlapping tool synonyms to the agent
- preserving unreleased facades or context-shaped duplicates only for sentimental compatibility
- letting change or alert artifacts stay presentational instead of feeding the reasoning loop
- replacing structured semantics with raw-tool improvisation
- splitting MCP services before semantic boundaries are stable
