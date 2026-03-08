# ADR 0001: Move Toward an Artifact-Oriented Investigation Workflow

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

## Decision

We will keep the current runtime topology for now:

- one user-facing read-only triage agent
- one custom `investigation-mcp-server` deployment

But we will evolve the internal tool surface toward an artifact-oriented investigation workflow.

The next architectural direction is:

- define stable investigation stages and artifacts
- refactor tool contracts around those stages and artifacts
- treat `build_*report` functions as end-of-flow rendering, not as the primary intelligence boundary

The target conceptual workflow is:

1. `normalize_incident_input`
2. `resolve_primary_target`
3. `collect_alert_evidence`
4. `collect_workload_evidence`
5. `collect_service_evidence`
6. `collect_node_evidence`
7. `collect_change_candidates`
8. `rank_hypotheses`
9. `render_investigation_report`

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

The key principle is:

> Prefer stronger investigation artifacts and workflow stages before adding more deployables or more agents.

## Consequences

### Positive

- clearer contracts for investigation steps
- easier testing of normalization, evidence gathering, correlation, and synthesis independently
- cleaner future path toward planner-led investigations
- easier future path to a true specialist investigation agent if and when that becomes justified

### Negative

- the current `investigation-mcp-server` remains a single deployment for longer
- some currently exposed tools may need to be renamed or reshaped
- transitional duplication may exist while old and new tool contracts coexist

## Explicit Non-Goals

This ADR does not choose:

- a remediation/write agent
- multiple MCP deployments by responsibility
- a full multi-agent specialist swarm
- a new GitOps or approval workflow

Those may come later, but they are not the next architectural step.

## Recommended Near-Term Refactor Direction

Near-term work should prioritize:

1. stabilizing canonical investigation artifacts
   - `InvestigationTarget`
   - `EvidenceBundle`
   - `ChangeCandidate`
   - `Hypothesis`
   - `InvestigationReport`

2. renaming or reshaping tools around those artifacts

3. making investigation flow more explicitly staged
   - normalize
   - resolve
   - collect
   - correlate
   - synthesize
   - render

4. keeping read-only posture and single-agent UX intact during the transition

## Future Revisit Trigger

We should revisit this decision when one or more of these becomes true:

- the investigation backend has stable artifact contracts and still feels too monolithic
- different evidence planes need different auth, scaling, or ownership boundaries
- the root triage agent is overloaded by tool count or reasoning complexity
- a true specialist investigation agent would provide clearer task/state separation than the current single-agent flow
