# ADR 0008: Add ArgoCD As Deterministic Delivery-State And Recent-Change Evidence

- Status: Proposed
- Date: 2026-03-11
- Related ADRs:
  - `docs/adr/0001-artifact-oriented-investigation-workflow.md`
  - `docs/adr/0002-external-step-artifact-submission.md`
  - `docs/adr/0004-bounded-exploratory-evidence.md`
  - `docs/adr/0005-unified-ingress-and-subject-resolution.md`
  - `docs/adr/0006-loki-as-complementary-observability-evidence.md`
  - `docs/adr/0007-alertmanager-as-deterministic-alert-state-evidence.md`

## Context

The investigation system can now validate several bounded evidence planes locally in kind:

- Prometheus-backed metrics evidence
- Loki-backed complementary log evidence
- Alertmanager-backed alert-state evidence

Those planes explain runtime symptoms well, but they do not fully answer a different class of operator question:

> what changed in delivery state, and is the current cluster state consistent with the intended GitOps state?

In real incidents, that question often matters as much as raw runtime evidence.
For example:

- a service starts returning 5xx after a recent rollout
- an alert fires because an app is degraded or only partially synced
- a deployment is healthy at the Kubernetes level but ArgoCD shows drift or a failed sync
- the runtime symptoms started immediately after a new revision was applied

This is especially relevant in environments that already use ArgoCD as the operational control plane for deployments.
In those environments, ArgoCD can provide evidence that Prometheus, Loki, and Alertmanager cannot:

- app sync status
- app health status
- current desired revision
- last successful or failed sync attempt
- degraded child resources
- the identity of the most recent revision or sync wave that changed the app

That creates a practical evidence gap in the current POC:

- the system can explain runtime symptoms
- the system cannot yet explain recent delivery state or likely rollout causality from the GitOps plane

However, ArgoCD also creates a design risk if introduced carelessly.
It is easy to over-scope ArgoCD into:

- full Git history reasoning
- uncontrolled repo browsing
- mutation or rollback suggestions coupled too tightly to diagnosis
- timing-sensitive integration tests that depend on webhook realism or external Git behavior

So the main question is not whether ArgoCD is useful.
It is how to add it in a way that remains:

- deterministic in local kind validation
- bounded and read-only
- additive to the current runtime evidence planes
- honest about what ArgoCD can and cannot prove

## Decision

The architecture should treat ArgoCD as:

> a complementary delivery-state and recent-change evidence source that can explain GitOps sync/health state and recent applied revisions, while leaving runtime diagnosis owned by the existing workload, service, and alert evidence planes.

This means:

- ArgoCD should be introduced first as a read-only peer evidence source for app state and recent applied revisions
- the first slice should focus on deterministic app-state and revision evidence, not full source-control analysis
- the first slice should attach Argo evidence to existing workload and service evidence artifacts rather than introduce a new primary planner plane
- ArgoCD should initially corroborate or explain likely rollout-related causes, not replace Prometheus, Loki, Kubernetes, or Alertmanager as the primary runtime diagnosis sources
- recent-change evidence should be bounded to what ArgoCD itself knows directly, such as:
  - current revision
  - sync result
  - operation state
  - degraded resources
  - possibly revision history if exposed cleanly by the chosen tool contract

## Why This Is Reasonable Now

The current architecture already proved a reusable pattern:

- use a real peer MCP integration
- keep evidence-plane contracts typed and auditable
- materialize peer results into product-owned artifacts
- validate the behavior with deterministic kind lanes

ArgoCD fits that pattern well if the first slice stays narrow.
It should not be introduced as "GitHub inside the cluster" or as a generic repo browser.

Instead, the first slice can stay deterministic by modeling ArgoCD as an app-state and recent-deployed-revision plane.
That is closer in spirit to Alertmanager than to GitHub:

- the important evidence is discrete control-plane state
- deterministic local validation matters more than ecosystem realism
- the goal is truthful corroboration, not autonomous change execution

This also means ArgoCD should not be stretched into the universal answer for change history.
ArgoCD is a strong source for:

- delivery state
- current and recent applied revisions
- the per-sync resource list inside one application

But it is not a complete, cluster-wide, resource-agnostic change ledger.
For broader "what changed for these resources across clusters?" questions, a later dedicated change-history plane is likely cleaner than forcing ArgoCD to carry that scope alone.

## First-Slice Scope

The first ArgoCD slice should do four things:

1. add ArgoCD endpoint and MCP settings / registry support
2. add a typed `ArgoCdMcpClient`
3. materialize Argo app sync/health/revision evidence into typed investigation artifacts attached to existing workload/service evidence
4. add a deterministic kind validation lane for app-state and recent-change correlation

The first slice should not:

- add a new primary `PlanStep` for ArgoCD
- mutate Argo apps
- trigger syncs, rollbacks, or refreshes as part of the investigation runtime
- depend on external Git provider APIs
- attempt full commit-level blame across repositories
- require webhook-driven realism to validate the behavior

## Recommended Evidence Role

The first ArgoCD role should be:

- preserve and query delivery-state truthfully
- corroborate whether an app is:
  - synced
  - out-of-sync
  - degraded
  - progressing
- preserve the currently applied revision and recent sync outcome
- surface likely rollout-related evidence when runtime symptoms align with recent app changes

It should not initially:

- determine the primary diagnosis by itself
- replace runtime evidence planes for workload/service/alert diagnosis
- invent causality beyond what ArgoCD state can directly support
- become the system-of-record for cluster-wide change history

In other words:

- ArgoCD can say "this app became degraded after revision X" or "this app is out of sync"
- ArgoCD should not, by itself, claim why a pod is crash-looping unless the runtime evidence planes support that claim
- ArgoCD can say "these resources were part of the last sync result for this app"
- ArgoCD should not, by itself, claim which PR or author caused that resource change

## Deterministic Local Strategy

The local kind strategy should prefer deterministic app-state shaping over full GitOps realism for the first slice.

That means:

- stand up an optional ArgoCD stack in kind only if one is not already available in the local lane
- use a small local test app or fixture app-of-one
- force known states through manifest changes or controlled invalid desired state, such as:
  - healthy and synced
  - out-of-sync
  - degraded after sync
  - failed sync attempt
  - recent revision change with stable health

This is preferable to a more realistic but unstable approach because it gives direct control over:

- app identity
- sync state
- health state
- revision transitions
- degraded child-resource evidence

The first slice should prove that the runtime can observe and report those states truthfully.
It does not need to prove every detail of ArgoCD's full reconciliation model.

## Shared Versus Non-Shared Logic

### Logic That Should Be Shared

These parts match the existing Prometheus/Loki/Alertmanager pattern:

- peer MCP client structure
- settings and cluster-registry endpoint selection
- route provenance
- external-step artifact submission
- kind optional overlay pattern
- retained-debug validation scripts

### Logic That Should Not Be Shared Blindly

ArgoCD evidence is not metrics evidence, log evidence, or alert-state evidence.
These parts should remain Argo-specific:

- app identity and lookup rules
- sync-state normalization
- revision-history materialization
- degraded-resource summarization
- wording around "recent changes" versus "proven cause"

The first slice should not fake symmetry by pretending ArgoCD is another observability backend.
It is a delivery-control-plane evidence source.

## Recent-Change Scope

The user value of ArgoCD is not only current health.
It is also recent applied change awareness.

The first slice should therefore try to include a bounded notion of recent change, but only where ArgoCD itself can support it directly.

Good first-slice candidates:

- current revision
- previous revision, if exposed by the app history API
- last sync started/finished timestamps
- operation phase and message
- the resources that are degraded or out-of-sync
- the resources listed in the most recent sync result, if exposed cleanly by the chosen contract

This is enough to support statements like:

- "the app is degraded and the most recent applied revision is X"
- "the app became out-of-sync after revision Y"
- "the cluster is healthy at runtime, but ArgoCD still reports drift"
- "these specific resources were part of the last sync result for the application"

This is not enough to support statements like:

- "commit X by person Y introduced the bug"
- "PR Z caused the outage"

Those stronger conclusions would belong to a later GitHub or SCM-oriented slice.

If the product later needs a more general answer to:

> what changed for these exact resources across clusters?

that likely belongs in a separate change-history capability backed by:

- GitOps deployment state, such as ArgoCD
- SCM enrichment, such as GitHub
- Kubernetes audit evidence

rather than by expanding the first ArgoCD slice until it becomes an overloaded replacement for all three.

## Integration With The Existing Kubernetes Model

ArgoCD should integrate with the current cluster-aware investigation model from the first slice.

That means:

- the canonical investigation target should remain a Kubernetes runtime target
- Argo application identity should be additive evidence, not canonical target identity
- Argo evidence should preserve destination cluster and namespace
- the runtime should be able to reconcile:
  - the requested incident cluster
  - the resolved Kubernetes target
  - the Argo application destination cluster and namespace

The first slice may still use a single ArgoCD endpoint.
But it should not defer cluster and destination awareness in the evidence model.

This keeps ArgoCD aligned with the existing cluster-registry and target-resolution approach without forcing multi-Argo-endpoint routing into phase 1.

## Future Provenance Constraints

If the product later grows a broader change-history capability, it should treat exact resource identity as the primary lookup key rather than Argo application identity.

That means a canonical key like:

- `(cluster_alias, api_group, kind, namespace, name)`

should remain the stable join surface for:

- Argo ownership and desired-state provenance
- Kubernetes audit-backed applied-state history
- SCM commit and PR enrichment
- any product-owned logical service or tenant grouping

Argo should therefore be treated as a delivery-state and ownership source, not as the universal identity layer.
A later provenance capability should prefer explicit ownership metadata and stable tracking fields over naming inference.

Good future constraints to preserve now:

- ambiguity is an acceptable result and better than guessing
- ownership should come from explicit tracking metadata where possible
- logical application identity should stay distinct from Argo `Application` identity
- multi-source and multi-cluster Argo topologies should be represented honestly, including conflicting or ambiguous ownership

A future provenance or change-history plane will likely need a product-owned index that joins:

- exact Kubernetes resource identity
- owning Argo application identity
- destination cluster and namespace
- resolved source repo/path/chart and revision
- optional logical app or tenant identifier
- audit-backed write history

This ADR does not expand Argo phase 1 to include that broader provenance system.
It only records those constraints so the bounded Argo delivery-state slice does not accidentally lock the product into a weaker resource-to-app model later.

## Validation Goals

The local validation lane should be able to prove all of these:

1. a test app can be observed through the ArgoCD peer
2. an investigation can preserve Argo app sync and health evidence truthfully
3. a recent applied revision can be surfaced as bounded recent-change evidence
4. a degraded or out-of-sync app can appear in route provenance without replacing runtime diagnosis
5. runtime symptoms and Argo delivery-state evidence can coexist in the same investigation artifact honestly

## Likely First Validation Matrix

The first local matrix should cover:

- healthy synced app
  - proves no false degradation
- out-of-sync app
  - proves drift evidence
- degraded app after a new revision
  - proves recent-change correlation
- failed sync or invalid desired state
  - proves operation-state evidence
- runtime issue plus Argo app revision context
  - proves additive evidence rather than route replacement

## Consequences

### Positive

- closes a major delivery-state evidence gap
- improves confidence in rollout-related investigations
- gives the agent bounded awareness of recent applied changes without requiring full SCM reasoning
- fits real homelab and work environments that already rely on ArgoCD
- reuses the successful peer-evidence and kind-validation pattern already proven elsewhere

### Negative

- adds another peer contract to maintain
- introduces more optional kind infrastructure
- may create pressure to overreach into source-control analysis before the bounded Argo slice is mature
- can encourage false rollout-causality claims unless report wording stays careful

## What This ADR Does Not Decide

This ADR does not decide:

- the exact external ArgoCD MCP server/tool contract
- whether the first slice should use an existing MCP server or a thin compatibility wrapper
- whether later slices should add GitHub or SCM evidence as a separate peer plane
- whether broader resource change history should become a dedicated capability backed by audit plus GitOps/SCM
- whether ArgoCD should ever become a remediation or rollback plane
- whether revision history beyond "current versus previous" is worth materializing in the first slice

## Recommended Next Step

The next implementation slice should be:

1. identify or adopt a concrete ArgoCD MCP contract for app status and history lookup
2. keep Argo as an additive enrichment on existing workload and service evidence rather than a new primary planner plane
3. add deterministic kind validation for healthy, out-of-sync, and degraded app states
4. keep ArgoCD read-only, bounded, and provenance-first in the first slice
