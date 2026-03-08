# Planner-Led Transition Plan

- Status: Draft
- Date: 2026-03-08
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
- compatibility for current routes and tool names

## Current State

The current codebase already has most of the internal structural pieces:

- `InvestigationTarget`
- `EvidenceBundle`
- `Hypothesis`
- `InvestigationAnalysis`
- artifact-native guidelines and correlation
- staged wrapper tool names
- bounded multi-hypothesis behavior

What is still missing is the behavior model:

- no explicit `InvestigationPlan`
- no iterative plan execution loop
- no distinction between control-plane and evidence-plane usage in the live agent behavior
- kagent prompt/config still mostly teaches report-first invocation

## End-State Shape

The target behavior model is:

1. classify the investigation mode
2. normalize and resolve the primary target
3. build an explicit investigation plan
4. execute a bounded evidence batch
5. update the plan based on what was learned
6. repeat until evidence is sufficient or exhausted
7. rank hypotheses
8. render the final report

This should use:

- control-plane tools for semantics and workflow
- evidence-plane tools for discovery and drill-down
- legacy report tools as transitional facades only

## Tool Roles

### Control-Plane Tools

These should remain product-owned:

- `normalize_incident_input`
- `resolve_primary_target`
- `build_investigation_plan`
- `update_investigation_plan`
- `rank_hypotheses`
- `render_investigation_report`

### Evidence-Plane Tools

These can come from `investigation-poc`, kagent built-ins, or other MCP tools:

- alert details / alert rule context
- recent changes / config history
- Prometheus metric breakdowns
- bounded logs
- raw Kubernetes object inspection
- connectivity checks
- bounded evidence helpers

### Compatibility Facades

These remain during transition:

- `build_investigation_report`
- `build_alert_investigation_report`
- `build_root_cause_report`

## Recommended Next Slices

### Slice 1: Add Explicit Planning Artifacts

Introduce internal planning objects:

- `InvestigationPlan`
- `PlanStep`
- `PlanStatus`
- `EvidenceBatch`
- optional `InvestigationMode` expansion if needed

Minimum behavior:

- an initial plan can be created from:
  - alert RCA
  - targeted investigation
  - factual/capacity analysis
- the plan is compact and bounded
- the plan can represent:
  - pending steps
  - completed steps
  - deferred steps
  - evidence-plane intent

Validation gate:

- unit tests for mode-aware plan construction
- plan shape tests for alert, targeted, and factual questions

### Slice 2: Add Iterative Plan Execution

Introduce explicit execution helpers:

- `execute_investigation_step(...)`
- `update_investigation_plan(...)`

The important behavior is:

- execute one bounded evidence batch
- update the plan from the resulting evidence
- select the next bounded step

The first implementation should stay conservative:

- no arbitrary long autonomous loops
- bounded number of steps
- no write actions

Validation gate:

- tests showing plan updates after evidence batches
- tests showing the next step changes based on the findings

### Slice 3: Separate Agent-Visible Control-Plane vs Evidence-Plane Surfaces

Once planning artifacts exist, make the external meaning of tools match the architecture.

Goals:

- keep compatibility facades
- make control-plane tools clearly canonical
- make evidence-plane tools clearly exploratory

Likely changes:

- make `render_investigation_report` a true render stage
- make `collect_change_candidates` the canonical staged change-review name
- keep old names only as compatibility aliases

Validation gate:

- service-level staged-vs-facade equivalence tests
- route/MCP tests proving canonical tools behave as described

### Slice 4: Introduce a Narrow Evidence-Plane Policy for the Agent

Do not expose every evidence tool at once.

First allow the agent to use a narrow set intentionally:

- control-plane:
  - `normalize_incident_input`
  - `resolve_primary_target`
  - `build_investigation_plan`
  - `update_investigation_plan`
  - `rank_hypotheses`
  - `render_investigation_report`
- evidence-plane:
  - a small bounded set of alert/rule context
  - changes/history
  - Prometheus breakdowns
  - bounded evidence helpers

Do not optimize around full raw-tool sprawl first.

Validation gate:

- agent routing tests for prompt/tool-choice behavior
- assertions on first tool and follow-up tool sequences

### Slice 5: Transition the kagent Skill Config from Report-First to Planner-Led

Only after the first four slices are real.

Prompt behavior should become:

- plan first
- gather bounded evidence batches
- re-plan
- synthesize late

Legacy facades remain available, but should stop being taught as the primary behavior model.

Validation gate:

- kind e2e validating planner-led investigations
- alert e2e
- targeted workload e2e
- operator-backed target e2e
- factual/capacity question e2e

## Immediate Recommendation

The next implementation move should be:

### Implement Slice 1

Why:

- it is the main missing concept in the current architecture
- it aligns the codebase with the ADR more than another naming refactor would
- it creates the foundation for intentional agent behavior changes later

What not to do yet:

- do not switch the kagent prompt/config yet
- do not expose a large raw evidence-plane surface yet
- do not remove `build_*report` facades yet
- do not split MCP deployments

## End-State E2E Expectations

When the target model is in place, e2e coverage should include:

- alert investigation:
  - plan creation
  - rule context
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

- switching the agent to full staged behavior before plan artifacts exist
- exposing overlapping tool synonyms to the agent
- hiding all evidence gathering inside `build_*report`
- replacing structured semantics with raw-tool improvisation
- splitting MCP services before semantic boundaries are stable
