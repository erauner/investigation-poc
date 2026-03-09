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
- kagent-visible tool catalog excludes:
  - `build_investigation_report`
  - `build_alert_investigation_report`
  - `collect_*_context`
  - retired aliases that no longer represent the planner-led model

### Slice 6: Transition the kagent Skill Config from Report-First to Planner-Led

Status: Planned

Only after the previous slices are real.

Prompt behavior should become:

- plan first
- gather bounded evidence batches
- update plan
- synthesize late

Legacy facades may remain briefly, but they should stop being taught as the primary behavior model. This slice should also decide whether `normalize_alert_input` and `normalize_incident_input` remain exposed as debug-oriented helpers or are removed from the intentional agent surface entirely.

Validation gate:

- kind e2e validating planner-led investigations
- alert e2e
- targeted workload e2e
- operator-backed target e2e
- factual/capacity question e2e

### Slice 7: Remove Transitional Report-First And Context Surfaces

Status: Planned

Once the planner-led kagent config is live and validated, remove the remaining transitional surfaces that no longer earn their keep:

- remove `build_investigation_report`
- remove `build_alert_investigation_report`
- remove `collect_workload_context`
- remove `collect_service_context`
- remove `collect_node_context`
- remove `collect_alert_context`

This slice should only proceed after the planner-led tool sequence is stable in e2e coverage. The goal is to stop carrying dual report-first and artifact-first entrypoints in the backend.

Validation gate:

- route and MCP tests proving the removed surfaces are gone
- service tests proving no canonical flow depends on the deleted entrypoints
- kagent config and prompt no longer reference the removed tools anywhere

## Immediate Recommendation

The next implementation move should be:

### Implement Slice 6

Why:

- the agent-visible catalog is now intentionally narrow
- the next meaningful gain is teaching the agent to use that catalog in a genuinely planner-led sequence
- the remaining work is now primarily prompt/config behavior and end-to-end validation rather than backend surface cleanup

What not to do yet:

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
