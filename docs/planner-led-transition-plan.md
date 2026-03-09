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
- further cleanup of exploratory context surfaces from the intentional agent-visible catalog
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

### Implement Slice 5

Why:

- the core engine and public control-plane surface are now cleaner
- the next meaningful gain is constraining what the agent is actually taught to use
- the remaining overlap is now mostly about intentional tool policy rather than deep architectural seams

What not to do yet:

- do not switch the kagent prompt/config yet
- do not expose a large raw evidence-plane surface yet
- do not introduce a generic raw-tool orchestration layer
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
