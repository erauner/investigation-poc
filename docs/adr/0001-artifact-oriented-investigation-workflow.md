# ADR 0001: Move Toward a Planner-Led, Artifact-Oriented Investigation Workflow

- Status: Accepted
- Date: 2026-03-08

## Context

The current investigation system has a good runtime shape:

- one user-facing read-only triage agent: `incident-triage`
- one custom investigation backend: `investigation-mcp-server`
- built-in kagent Kubernetes tools for raw inspection
- a higher-level custom MCP layer for alert normalization, context collection, correlation, and reporting

That shape has been effective for shipping and validating:

- generic workload investigation
- operator-backed target resolution
- explicit alert entrypoints
- Claude Code and Claude Desktop read-only investigation flows

However, the current custom MCP surface still mixes several abstraction levels:

- intake / normalization
- target resolution
- evidence collection
- correlation / change review
- synthesis / reporting

This creates pressure on prompt logic and makes the backend contracts harder to reason about. It also makes it harder to evolve toward a richer, planner-led investigation flow where the system can:

1. fetch issue details
2. get alert or rule context
3. inspect recent changes
4. query metrics
5. inspect workloads and logs
6. correlate evidence
7. synthesize and render a final report

An alternative considered was splitting the current custom MCP surface into multiple MCP servers by responsibility. We are explicitly not choosing that as the next move.

We have also learned that an artifact-oriented internal model alone is not enough. External investigation systems that feel easy to follow tend to make the investigation plan visible and treat evidence planes as first-class:

- issue / alert details
- alert rule or runbook context
- recent changes
- metrics
- workload or node state
- logs
- final synthesis

Those systems often expose a planner-led workflow even if the runtime topology is still relatively simple. That is the direction we now want to move toward.

## Decision

We will keep the current runtime topology for now:

- one user-facing read-only triage agent
- one custom `investigation-mcp-server` deployment

But we will evolve the system toward a planner-led, iterative, artifact-oriented investigation workflow.

The next architectural direction is:

- define stable investigation stages and artifacts
- refactor tool contracts around those stages and artifacts
- make the investigation plan, evidence planes, and iterative execution loop explicit
- treat `build_*report` functions as compatibility facades and end-of-flow rendering, not as the primary intelligence boundary

The target conceptual workflow is:

1. `normalize_incident_input`
2. `resolve_primary_target`
3. `build_investigation_plan`
4. `execute_investigation_step`
5. `update_investigation_plan`
6. repeat evidence collection and re-planning until confidence is sufficient or evidence is exhausted
7. `rank_hypotheses`
8. `render_investigation_report`

This does not require multiple new MCP deployments yet. The first boundary change is semantic, not operational.

## Why

This direction preserves the benefits of the current system:

- one clear user-facing triage path
- one deployable custom backend
- read-only posture
- lower operational complexity

While improving the parts that currently cause architectural drag:

- overloading one tool surface with mixed responsibilities
- tool contracts shaped too closely to current implementation details
- prompt logic compensating for muddy backend semantics
- difficulty evolving toward a richer investigation flow without adding ad hoc behaviors
- a report-first workflow that hides the actual investigation stages from both the agent and the user
- difficulty supporting both alert RCA and generic factual/capacity questions with the same top-level abstraction

The key principle is:

> Prefer stronger investigation artifacts, explicit planning, iterative evidence gathering, and evidence-plane workflows before adding more deployables or more agents.

Another core principle is:

> Let the agent explore evidence planes directly, but keep normalization, target resolution, planning semantics, hypothesis ranking, and final rendering inside the product layer.

## Current State vs Target State

### Where We Are Now

Today, the system is in a transitional but healthy state:

- runtime topology is intentionally simple
  - one user-facing read-only agent
  - one custom investigation backend
- internal artifacts now exist
  - `InvestigationTarget`
  - `EvidenceBundle`
  - `Hypothesis`
  - `InvestigationAnalysis`
  - `GuidelineContext`
- internal flow is increasingly staged
  - normalize / resolve
  - collect evidence
  - rank hypotheses
  - render report
- staged wrapper tools now exist alongside legacy tools

However, externally the system is still largely legacy-shaped:

- prompt/config behavior is still mostly report-first
- `build_investigation_report` and `build_alert_investigation_report` remain the primary user-facing mental model
- the plan is still mostly implicit
- evidence planes are not yet exposed as a deliberate workflow

In practical terms, the agent currently behaves more like:

1. normalize or infer a target
2. call a high-level report-building tool early
3. optionally enrich with related data or drill-down tools
4. return a final answer

That means much of the real investigation structure is hidden behind a small number of high-level calls.

### Where We Want to Get To

We want the system to feel more like a visible investigation workflow without abandoning the current runtime simplicity.

The intended end state is:

- one user-facing triage agent
- one deployable investigation backend
- a planner-led read-only investigation flow
- explicit evidence planes
- iterative re-planning between evidence batches
- a final rendered report only after the investigation has enough evidence

Conceptually, the desired investigation loop is:

1. understand the issue
2. identify the likely target
3. build a short investigation plan
4. gather an initial evidence batch
5. update the plan based on what was learned
6. gather the next targeted evidence batch
7. narrow the most likely causes
8. render a final report

In practical terms, we want the agent behavior to evolve toward:

1. inspect the incoming issue type
   - alert-shaped RCA
   - generic targeted investigation
   - factual/capacity/resource question
2. create a short, explicit investigation plan
3. gather a small evidence batch by plane in parallel where appropriate
4. refine the plan based on the previous evidence batch
5. choose the next best targeted queries
6. repeat until confidence is sufficient or evidence is exhausted
7. rank plausible causes
8. render the final answer only after enough evidence exists

This is the key behavioral change:

> The agent should stop behaving like a report requester and start behaving like an investigator with a visible plan.

## Invocation Pattern Shift

### Current Invocation Pattern

The current system still tends to encourage this style:

1. `build_investigation_report(...)`
2. optionally `collect_*_context(...)`
3. optionally `collect_correlated_changes(...)`
4. return answer

For alerts, the current live behavior is often still effectively:

1. normalize alert
2. resolve target
3. call `build_alert_investigation_report(...)`
4. optionally enrich

This is simple, but it hides the actual investigative reasoning and keeps the primary mental model centered on report generation.

### Desired Invocation Pattern

The desired behavior is more like:

1. `normalize_incident_input(...)`
2. `resolve_primary_target(...)`
3. `build_investigation_plan(...)`
4. execute the first evidence batch
   - `collect_alert_evidence(...)`
   - `collect_workload_evidence(...)`
   - `collect_service_evidence(...)`
   - `collect_node_evidence(...)`
   - `collect_change_candidates(...)`
   - future alert-rule or runbook context tools
5. reassess findings and update the plan
6. execute the next targeted evidence batch
7. repeat until the investigation has enough evidence
8. `rank_hypotheses(...)`
9. `render_investigation_report(...)`

This does not mean every investigation must call every tool. It means the staged, iterative investigation flow becomes the primary behavior model.

### Why This Matters

This shift makes the system:

- easier to reason about
- easier to debug
- easier to evaluate
- easier to adapt to different investigation types
- closer to the behavior of stronger planner-led investigation systems
- better aligned with workflows where the next query depends on what the last batch of evidence revealed

It also makes the distinction between:

- evidence gathering
- analysis
- rendering

visible in the actual agent workflow, not just hidden inside backend implementation details.

## Tool Taxonomy We Are Moving Toward

The target system should distinguish between three kinds of tools.

### 1. Control-Plane Tools

These define the investigation structure and should remain product-owned:

- `normalize_incident_input(...)`
- `resolve_primary_target(...)`
- `build_investigation_plan(...)`
- `update_investigation_plan(...)`
- `rank_hypotheses(...)`
- `render_investigation_report(...)`

These tools should stay opinionated and consistent. They are where we encode:

- issue normalization
- operator target resolution
- mode-aware planning
- bounded hypothesis ranking
- report contract semantics

### 2. Evidence-Plane Tools

These should be more directly available to the agent because they support iterative exploration:

- alert rule lookup
- recent changes / config history
- Prometheus metric breakdowns
- bounded log retrieval
- raw Kubernetes object inspection when needed
- connectivity checks where useful
- bounded target-scoped evidence helpers such as:
  - `collect_alert_evidence(...)`
  - `collect_workload_evidence(...)`
  - `collect_service_evidence(...)`
  - `collect_node_evidence(...)`
  - `collect_change_candidates(...)`

The intent is not to expose every possible raw tool. The intent is to expose evidence planes clearly enough that the agent can iterate based on what it has learned.

These evidence-plane tools do not all need to be implemented by `investigation-poc`.

Where possible, we should intentionally reuse:

- kagent built-in tool servers
- other MCP servers already available in the environment
- external or shared read-only MCP integrations that expose bounded evidence access

This means the agent may gather evidence from tools we do not own, as long as:

- the tool is read-only or otherwise safely bounded for this phase
- the evidence plane is relevant and non-overlapping enough to justify the added tool
- the tool is understandable enough that the agent can call it intentionally
- the resulting evidence can still be interpreted within our investigation semantics

Examples of evidence planes that may come from non-owned MCP tools:

- alert rule and alert metadata lookup
- Prometheus metric breakdowns
- runbook or documentation retrieval
- change history / deployment history
- ArgoCD or GitOps read-only inspection
- bounded connectivity checks
- raw Kubernetes inspection beyond what our product layer should abstract

The key distinction is:

- evidence retrieval can come from multiple MCP sources
- investigation semantics should still be owned by our product layer

## Responsibility Split

### What `investigation-poc` Should Own

`investigation-poc` should own the semantics that define the product:

- issue normalization
- convenience-target and operator-target resolution
- investigation planning semantics
- artifact definitions and transitions
- bounded hypothesis ranking
- confidence softening and ambiguity handling
- final rendered investigation output

This is the reusable investigation logic that should remain consistent regardless of which evidence sources are available.

### What Other MCP Tools Can Own

Other MCP tools can own evidence retrieval where they already provide useful, bounded capabilities:

- raw Kubernetes inspection
- log retrieval
- Prometheus and observability lookups
- change history
- runbook retrieval
- GitOps / ArgoCD read-only inspection
- narrow environment-specific evidence sources

We do not need to reimplement every evidence plane ourselves just because the investigation product will use it.

### What This Means for the Agent

The user-facing agent should be able to combine:

- our product-owned control-plane tools
- our bounded evidence helpers where helpful
- other MCP-provided evidence tools that we do not own

That combination is intentional. The goal is not to hide all evidence gathering behind our custom service. The goal is to let the agent gather evidence flexibly while keeping the investigation semantics coherent.

### 3. Compatibility / Legacy Facade Tools

These exist to preserve compatibility during the transition:

- `build_investigation_report(...)`
- `build_alert_investigation_report(...)`
- `build_root_cause_report(...)`

They should remain available while the staged planner-led flow is taking shape, but they should no longer be treated as the long-term primary mental model for every investigation.

## Balance We Are Intentionally Trying to Strike

We are explicitly not choosing either extreme:

- not a black-box model where the agent hides almost everything behind one report tool
- not a raw-tool sprawl where the agent improvises all semantics from scratch

The intended balance is:

- the agent should be free to explore evidence planes iteratively
- the product layer should still own normalization, target resolution, planning semantics, hypothesis ranking, and final rendering

In other words:

- evidence gathering should become more direct and iterative
- investigation semantics should remain structured and reusable

This is the balance that should let the system feel more like a real investigator without giving up consistency, boundedness, or maintainability.

## Example Target Workflows

### Alert Investigation Example

For an alert like `EnvoyHighErrorRate`, the desired behavior is closer to:

1. normalize the alert and resolve the most likely target or investigation scope
2. build a short plan
3. fetch alert details and alert rule using direct evidence-plane tools
4. inspect recent changes around the alert window
5. localize the failing backend or route from metrics
6. inspect the implicated service/workload and logs
7. rank plausible hypotheses
8. render the final investigation report

This should feel like an investigation with visible intermediate steps and re-planning, not a single opaque report call.

### Generic Capacity / Factual Analysis Example

For a generic question like:

> what are the biggest resource consumers in my cluster?

the system should not force a root-cause-report flow. It should instead:

1. identify the question as factual/resource-analysis oriented
2. build a lightweight plan for the relevant evidence
3. collect the first relevant evidence batch using direct resource/capacity evidence tools
4. refine if additional evidence is needed
5. summarize findings directly

This is an important distinction: not every investigation-like question is an RCA workflow.

### Operator-Backed Target Investigation Example

For an operator target like `Backend/crashy`, the desired flow is:

1. normalize the convenience target
2. resolve the primary operational target
3. build a short investigation plan
4. collect evidence for the resolved workload and relevant changes
5. refine the next steps if the first evidence batch is inconclusive
6. rank plausible hypotheses
7. preserve the original operator target context in the rendered answer

## Consequences

### Positive

- clearer contracts for investigation steps
- easier testing of normalization, evidence gathering, correlation, and synthesis independently
- cleaner future path toward planner-led investigations
- easier future path to a true specialist investigation agent if and when that becomes justified
- easier to support both alert-driven RCA and generic factual analysis without conflating them

### Negative

- the current `investigation-mcp-server` remains a single deployment for longer
- some currently exposed tools may need to be renamed or reshaped
- transitional duplication may exist while old and new tool contracts coexist
- some existing internal refactors may need to be revisited if they do not align with the eventual planner-led public workflow

## Explicit Non-Goals

This ADR does not choose:

- a remediation/write agent
- multiple MCP deployments by responsibility
- a full multi-agent specialist swarm
- a new GitOps or approval workflow

Those may come later, but they are not the next architectural step.

## Recommended Near-Term Direction

Near-term work should prioritize:

1. stabilizing canonical investigation artifacts
   - `InvestigationTarget`
   - `EvidenceBundle`
   - `ChangeCandidate`
   - `Hypothesis`
   - `InvestigationReport`

2. introducing an explicit investigation planning artifact
   - `InvestigationPlan`
   - `PlanStep`
   - `PlanStatus`
   - `EvidenceBatch`
   - mode-aware planning for alert, targeted RCA, and factual analysis

3. renaming or reshaping tools around those artifacts and stages

4. explicitly separating control-plane tools from evidence-plane tools

5. making investigation flow more explicitly staged
   - normalize
   - resolve
   - plan
   - collect
   - correlate
   - synthesize
   - render

6. keeping read-only posture and single-agent UX intact during the transition

7. intentionally changing the agent invocation pattern so that iterative staged investigation becomes the default behavior model instead of report-first calls

## Future Revisit Trigger

We should revisit this decision when one or more of these becomes true:

- the investigation backend has stable artifact contracts and still feels too monolithic
- different evidence planes need different auth, scaling, or ownership boundaries
- the root triage agent is overloaded by tool count or reasoning complexity
- a true specialist investigation agent would provide clearer task/state separation than the current single-agent flow
