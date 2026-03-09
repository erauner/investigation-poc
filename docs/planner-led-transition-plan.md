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

- a canonical `InvestigationState` or equivalent state artifact
- ranking and rendering that operate on state rather than request-time recollection wrappers
- a real alert-plane artifact instead of alert-shaped runtime indirection
- a tighter public tool surface with redundant facades and context-shaped duplicates removed or demoted
- agent/runtime behavior that intentionally prefers the planner-led path

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

Status: Next

The next semantic seam should be explicit investigation state, not a broader executor.

Goals:

- introduce a canonical state artifact for collected investigation progress
- make `rank_hypotheses` consume state, not just request wrappers
- make `render_investigation_report` consume state, not just request wrappers
- move execution-time state assembly out of `planner.py` so planning remains purely control-plane
- make externally gathered evidence composable into the control plane later

Likely changes:

- add `InvestigationState` or equivalent
- add narrow collected-artifact/state payloads
- keep request-based report wrappers only as thin facades
- make state the canonical input to analysis and rendering

Validation gate:

- service tests proving ranking/rendering consume state rather than request-time recollection
- tests proving collected change and alert artifacts can affect reasoning before final render

### Slice 4: Narrow and Clean the Public Surface

Status: Planned

Once state is canonical, make the public surface match the architecture.

Goals:

- make control-plane tools clearly canonical
- make evidence-plane tools clearly exploratory
- remove or demote transition-only and redundant surfaces

Likely changes:

- prefer `collect_change_candidates` over `collect_correlated_changes`
- remove `build_root_cause_report` first
- demote or retire `collect_*_context` from the intentional agent surface
- keep only thin request-based wrappers that still earn their place

Validation gate:

- route/MCP tests proving canonical tools behave as described
- tests proving removed or demoted surfaces no longer carry unique logic

### Slice 5: Introduce a Narrow Evidence-Plane Policy for the Agent

Status: Planned

Do not expose every evidence tool at once.

First allow the agent to use a small intentional set:

- control-plane:
  - `normalize_incident_input`
  - `resolve_primary_target`
  - `build_investigation_plan`
  - `execute_investigation_step`
  - `update_investigation_plan`
  - `rank_hypotheses`
  - `render_investigation_report`
- owned evidence-plane:
  - `collect_workload_evidence`
  - `collect_service_evidence`
  - `collect_node_evidence`
  - `collect_change_candidates`
- selected non-owned evidence tools:
  - alert/rule context
  - metrics breakdowns
  - bounded logs
  - bounded change history
  - raw Kubernetes follow-up where needed

Validation gate:

- agent routing tests for prompt/tool-choice behavior
- assertions on first tool and follow-up tool sequences

### Slice 6: Transition the kagent Skill Config from Report-First to Planner-Led

Status: Planned

Only after the previous slices are real.

Prompt behavior should become:

- plan first
- gather bounded evidence batches
- update plan
- synthesize late

Legacy facades may remain briefly, but they should stop being taught as the primary behavior model.

Validation gate:

- kind e2e validating planner-led investigations
- alert e2e
- targeted workload e2e
- operator-backed target e2e
- factual/capacity question e2e

## Immediate Recommendation

The next implementation move should be:

### Implement Slice 3

Why:

- Slice 2 made execution bounded and explicit
- the next missing seam is canonical investigation state
- state-first ranking and rendering are required before broader external evidence composition makes sense

What not to do yet:

- do not switch the kagent prompt/config yet
- do not expose a large raw evidence-plane surface yet
- do not expand the executor into a generic raw-tool orchestrator
- do not split MCP deployments

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
