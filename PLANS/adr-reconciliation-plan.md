# ADR Reconciliation Plan

## Purpose

This plan translates the current ADR direction into a staged code migration path.

Source of truth when in doubt:

1. `docs/adr/0003-langgraph-execution-shell.md`
2. `docs/adr/0005-unified-ingress-and-subject-resolution.md`
3. `docs/adr/0004-bounded-exploratory-evidence.md`
4. `docs/planner-led-transition-plan.md`

The main migration risk is no longer architectural confusion in the docs. It is partial adoption in code:

- old eager-collapse behavior still active in ingress
- newer soft-primary / bounded-execution semantics already present downstream
- scout behavior evolving, but without a fully explicit planner-seed seam yet

This document is intentionally anchored to the actual merged code on `main`, not just the desired end state.

## Current Codebase Reality

### Already landed

These pieces are already in place and should be treated as migration foundations, not future work:

- `subject_context` exists in `NormalizedInvestigationRequest` and `InvestigationTarget`
  - `src/investigation_service/models.py`
- `StepExecutionInputs` already carries:
  - `primary_subject`
  - `related_subjects`
  - `src/investigation_service/models.py`
- planner already preserves investigation-shaped ambiguity instead of silently degrading everything to factual
  - `src/investigation_service/planner.py`
- reporting/runtime already preserves `subject_context` through state alignment and handoff flows
  - `src/investigation_service/reporting.py`
  - `src/investigation_service/state.py`
- direct `execution_context` reuse is already guarded against mixed-incident replay
  - `src/investigation_service/reporting.py`

### Still architecturally misaligned with the ADRs

These are the main code-level gaps between current `main` and the updated ADR direction:

1. Ingress still owns direct semantic collapse into an exact normalized request.
   - `normalize_ingress_request(...)` builds `NormalizedInvestigationSubjectSet`
   - `normalized_request_from_subject_set(...)` immediately forces that into `NormalizedInvestigationRequest`
   - `_normalized_request_for_focus(...)` is still the eager-collapse core
   - `src/investigation_service/ingress.py`

2. Ingress still depends on CR-backed operational rewriting.
   - `IngressDeps` still includes:
     - `canonical_target`
     - `scope_from_target`
     - `get_backend_cr`
     - `get_frontend_cr`
     - `get_cluster_cr`
     - `find_unhealthy_pod`
   - this is broader than the ADR's intended "meaning first, bounded commitment later" seam
   - `src/investigation_service/ingress.py`

3. Planner still has no first-class planner-seed seam.
   - current flow is still effectively:
     - ingress request
     - subject set
     - normalized request
     - investigation target
   - planner has a useful `_subject_set_and_normalized(...)` bridge, but that is not yet a true planner-seed model
   - `src/investigation_service/planner.py`

4. Legacy convenience collapsing still lives outside the new seam.
   - examples:
     - `resolve_backend_convenience_target(...)`
     - frontend / cluster convenience resolution
     - vague workload resolution
   - these should eventually be either planner-seed responsibilities or deterministic narrowing steps
   - `src/investigation_service/planner.py`

5. Scout code is still framed primarily around evidence expansion.
   - `ExploratoryScoutContext` currently carries:
     - capability
     - step id
     - plane
     - execution inputs
     - baseline assessment
     - baseline summary
     - hints
   - it does not yet explicitly model:
     - scout intent
     - focus narrowing
     - planner-owned promotion of scout recommendations
   - `src/investigation_service/exploration.py`

6. Provenance/reporting still lacks an explicit focus-divergence trace.
   - `subject_context` is preserved
   - but there is not yet a dedicated rendered chain for:
     - requested subject
     - soft primary focus
     - bounded execution focus
     - why focus changed
   - this is a real gap between the ADR wording and operator-facing truthfulness

### Important correction to earlier migration sketches

The codebase is no longer at "introduce subject context" stage.

That means the first code migration phase should not be "add subject context everywhere."
It should be:

- introduce a real planner-seed seam
- route current collapse through it
- then move eager-collapse logic out of ingress incrementally

## End-State Target

The intended steady-state architecture is:

1. Ingress owns semantic understanding.
   - dominant scope
   - subject candidates
   - related subjects
   - ambiguity
   - soft primary focus

2. Planner-seed derivation owns bounded semantic commitment.
   - obvious bounded execution focus
   - deterministic narrowing
   - or bounded scout narrowing eligibility

3. Scouts remain bounded runtime helpers.
   - evidence expansion
   - focus narrowing recommendation
   - never semantic ownership
   - never direct promotion to execution focus

4. Reporting preserves focus divergence truthfully.
   - requested subject
   - soft primary focus
   - bounded execution focus
   - focus change reason
   - related subjects considered

5. Execution remains single-focus for now.
   - richer semantic model first
   - multi-target planning deferred

## Gaps Not To Lose Track Of

These are easy to miss while doing the migration:

- ambiguity taxonomy exists in docs, but not yet as a stable code contract
- relation vocabulary in code is still narrower than the docs
  - code currently uses `candidate`, `member`, `dependency`, `related`
- cross-namespace related subjects are possible semantically, but there is no strong lifecycle/cap enforcement yet
- generic evidence planes must not become shadow semantic resolvers
- more probing must not automatically mean higher confidence

## Recommended Phases

### Phase 1: Formalize The Semantic Seams In Code

Goal:
- make the new boundaries explicit without changing too much runtime behavior yet

Deliverables:
- add a first-class `InvestigationPlannerSeed` model in `src/investigation_service/models.py`
- add a stable ambiguity taxonomy contract aligned with ADR 0005
- add explicit planner-seed outcome semantics
  - obvious execution focus
  - deterministic narrowing required
  - bounded scout narrowing eligible
  - bounded ambiguity
- add a stable relation vocabulary contract aligned with ADR 0005
- add explicit scout intent vocabulary
  - likely `evidence_expansion`
  - likely `focus_narrowing`
- add a lightweight focus-divergence provenance model, even if only internal at first

Why this phase comes first:
- current code already has downstream `subject_context`
- the missing seam is planner-seed, not subject propagation

### Phase 2: Introduce Planner-Seed Derivation As A Real Code Path

Goal:
- create the new semantic bridge without deleting existing behavior immediately

Primary files:
- `src/investigation_service/planner.py`
- likely a new `src/investigation_service/planner_seed.py`
- `src/investigation_service/models.py`

Deliverables:
- add `planner_seed_from_subject_set(...)`
- route `resolve_primary_target(...)` and `build_investigation_plan(...)` through planner-seed derivation
- allow planner-seed derivation to wrap existing collapse behavior internally at first, so commitment is centralized before it is simplified
- preserve trivial cases
  - direct `statefulset/name`
  - direct `pod/name`
  - direct `service/name`
- keep returning current downstream contracts for compatibility

Important rule:
- planner-seed becomes the preferred semantic bridge
- no new shortcuts directly from subject set to exact target outside that seam

### Phase 3: Move Eager Exact-Target Collapse Out Of Ingress

Goal:
- ingress stops at meaning, not final operational commitment

Primary files:
- `src/investigation_service/ingress.py`
- `src/investigation_service/planner.py`
- any new planner-seed module

Deliverables:
- reduce or remove `_normalized_request_for_focus(...)`
- move CR-backed backend/frontend/cluster operational rewriting out of ingress
- move profile promotion tied to exact target collapse out of ingress
- keep in ingress:
  - scope resolution
  - candidate extraction
  - related refs
  - ambiguity
  - soft primary focus

Explicit non-goal:
- do not remove useful deterministic extraction from ingress
- only remove early commitment behavior

### Phase 4: Align Scouts To The New Planner-Owned Narrowing Seam

Goal:
- scouts understand richer semantic context without taking ownership of meaning

Primary files:
- `src/investigation_service/exploration.py`
- `src/investigation_service/execution_policy.py`
- `src/investigation_orchestrator/*scout.py`
- `src/investigation_orchestrator/evidence_runner.py`

Deliverables:
- enrich scout context with:
  - intent
  - primary subject
  - related subjects
  - planner-seed or execution-focus context as needed
- distinguish scout-local outcomes:
  - evidence delta
  - focus recommendation
  - no useful change
- ensure only planner-owned code can promote a scout recommendation into bounded execution focus

Important guard rail:
- scout recommendation is advisory
- promotion remains deterministic and planner-owned

### Phase 5: Truthful Provenance And Reporting

Goal:
- make the softer architecture understandable and trustworthy

Primary files:
- `src/investigation_service/reporting.py`
- `src/investigation_service/state.py`
- possibly analysis/rendering code paths

Deliverables:
- preserve and render:
  - requested subject
  - soft primary focus
  - bounded execution focus
  - focus change reason
  - related subjects considered
- land focus-divergence truthfulness in debug/provenance rendering first, then tighten operator-facing wording once the semantic chain is stable
- ensure confidence can stay flat or decrease when evidence stays ambiguous or contradictory

This phase is important because the architecture will feel slippery without it, even if the semantics are correct.

### Phase 6: Remove Transitional Eager-Collapse Helpers

Goal:
- prevent architectural backsliding

Candidates:
- ingress-local helpers whose main job is exact-target collapse
- duplicate convenience helpers that bypass planner-seed derivation
- CR-backed ingress rewriting that only exists to preserve the old flat-target model

Important rule:
- preserve public surfaces where useful
- do not preserve internal helper lineage for its own sake
- when a helper exists only to preserve eager exact-target collapse or duplicate pre-planner-seed behavior, the default action is to remove it or fold it behind the new seam rather than retain it as legacy structure

### Phase 7: Reevaluate Multi-Target Planning Later

Goal:
- defer broader runtime changes until the single-focus softer model is proven

Not in scope now:
- grouped execution across multiple namespaces
- cross-context grouped execution
- family-scoped parallel investigations

Prerequisite:
- planner-seed plus bounded execution focus must be stable first

### Phase 8: Harden Stable Stage-Boundary Schemas

Goal:
- lock in the stable end-state seam models after the migration settles

Why this phase is late:
- adding rigid schemas too early would freeze transitional shapes
- once planner-seed, scout alignment, and provenance semantics are real, schema hardening becomes a force multiplier for future refactors instead of extra ceremony

Primary targets:
- `NormalizedInvestigationSubjectSet`
- `InvestigationPlannerSeed`
- `ExploratoryNodeContext`
- scout outcome model
- final reconciled artifact/reconciliation models

Deliverables:
- tighten boundary models where multiple subsystems depend on the same meaning
- add stronger validation only where semantic drift would be costly
- optionally add JSON-schema export or snapshot validation for the most important seam contracts
- keep probe-local scratch state and small internal helper structures lightweight

Explicit non-goal:
- do not schema every internal helper
- do not introduce a giant ontology for raw tool outputs
- do not harden seam models before their behavior has stabilized

## Suggested PR Breakdown

### PR 1: Planner-Seed Seam Introduction

Includes:
- `InvestigationPlannerSeed`
- planner-seed outcome vocabulary
- initial planner-seed derivation path
- no major ingress behavior removal yet

### PR 2: Ingress De-Eagering

Includes:
- route collapse through planner-seed
- reduce ingress-local exact-target collapse
- move CR-backed operational rewriting out of ingress

### PR 3: Scout Intent And Focus-Narrowing Alignment

Includes:
- scout intent
- planner-owned focus-promotion seam
- scout-local output distinction

### PR 4: Focus-Divergence Provenance And Reporting

Includes:
- explicit trace/rendering for:
  - requested subject
  - soft primary focus
  - bounded execution focus
  - why focus changed

### PR 5: Cleanup And Removal

Includes:
- remove transitional eager-collapse helpers
- reduce duplicate convenience paths
- tighten tests so planner-seed remains the only preferred semantic bridge

### PR 6: Stable Seam Schema Hardening

Includes:
- formalize the settled planner-seed and scout seam models
- add stricter validation/tests for stable stage boundaries
- optionally add schema export/snapshots for the key end-state contracts

## Practical Examples

### Example A: Clean direct target

Input:
- `Investigate statefulset/newmetrics-db in tenant-120330-prod on jed1`

Expected shape:
- ingress:
  - soft primary focus = `statefulset/newmetrics-db`
- planner-seed:
  - bounded execution focus = `statefulset/newmetrics-db`
- scouts:
  - not needed for focus narrowing

### Example B: Express family with possible dependency involvement

Input:
- `newmetrics is failing in tenant-120330-prod on jed1, maybe db too`

Expected shape:
- ingress:
  - soft primary focus = `express_cluster/newmetrics`
  - related subjects include `newmetrics-db`
- planner-seed:
  - may or may not bind an immediate bounded execution focus
- scout:
  - if needed, can help narrow whether backend or DB should be inspected first
- report:
  - must preserve if execution focus later became `statefulset/newmetrics-db`

### Example C: Cross-namespace dependency context

Input:
- `tenant-x is failing in jed1, maybe shared-cache in platform-services is involved`

Expected shape:
- ingress:
  - dominant scope remains tenant-x namespace
  - related subject in `platform-services` is preserved as context
- planner-seed:
  - still chooses one bounded execution focus for now
- scout:
  - may inspect the external dependency only if policy and bounded narrowing justify it

## Recommended Test Additions By Phase

### Planner-seed introduction

- planner-seed trivial pass-through for clean targets
- planner-seed bounded ambiguity outcome for mixed unrelated subjects
- planner-seed deterministic narrowing path for ambiguous but scoped requests

### Ingress de-eagering

- ingress no longer performs cluster/backend/frontend CR-backed exact-target collapse directly
- equivalent old cases still succeed through planner-seed

### Scout alignment

- focus-narrowing scout cannot directly mutate execution focus
- planner-owned promotion consumes scout recommendation explicitly

### Provenance/reporting

- requested subject, soft primary focus, and bounded execution focus all survive runtime and reporting
- focus divergence is visible in output/debug trace

## Explicitly Out Of Scope For This Plan

- full multi-target planning
- grouped alert execution
- cross-context grouped execution
- a new ADR for multi-target planning before the single-focus planner-seed seam is proven

## Maintenance Note

Keep this document updated whenever one of these becomes true:

- a phase is mostly complete
- a planned phase is no longer needed because code already landed
- a new helper is introduced that touches semantic commitment
- a scout path starts influencing execution focus

If code and ADRs appear to disagree, reconcile toward the ADRs unless there is a deliberate new decision recorded in docs.

Migration hygiene rule:

- in every implementation plan and follow-up PR, treat obsolete eager-collapse helpers as removal-or-repurpose candidates by default
- do not preserve duplicate internal paths as legacy compatibility unless they still protect a public contract that is intentionally being kept stable
