# ADR 0005: Unify Investigation Ingress Around Subject Resolution

- Status: Proposed
- Date: 2026-03-11
- Related ADRs:
  - `docs/adr/0001-artifact-oriented-investigation-workflow.md`
  - `docs/adr/0003-langgraph-execution-shell.md`
  - `docs/adr/0004-bounded-exploratory-evidence.md`

## Context

The planner-led runtime is now strong once it starts from:

- one bounded execution focus
- one bounded plan
- one deterministic runtime spine

The next pressure point is no longer only runtime control.
It is ingress and subject resolution.

Real investigation requests increasingly arrive as:

- freeform generic questions
- single alerts
- groups of related alerts
- direct references to Deployments, StatefulSets, pods, Frontend CRs, Backend CRs, or services
- mixed Slack-style text mentioning several related things at once

The current simpler assumptions are becoming too narrow:

- one `target`
- or one `alertname`

That was acceptable while planning and runtime control were still being stabilized.
It is now the next semantic bottleneck.

The domain also has several important distinctions that should be modeled explicitly:

- Kubernetes context / datacenter
  - where the investigation executes
- tenant namespace
  - the customer isolation and search boundary
- Express cluster
  - a logical application grouping made up of Backend and Frontend resources only
- DB workload
  - usually a separate StatefulSet or pod set in the same namespace that may be operationally related to an Express cluster, but is not semantically part of it
- generic Kubernetes workloads
  - Deployments, StatefulSets, pods, and services that must remain first-class even when they are not part of any Express cluster

The runtime evidence layer also has a common substrate:

- many workload types eventually converge on pods for runtime evidence

But that does not mean pod should become the only semantic subject type.

## Decision

The next ingress refactor should be:

> unify ingress around subject-centric normalization that resolves scope, subject candidates, related refs, ambiguity, and one soft primary focus; defer exact execution-target collapse until planner-seed derivation or bounded evidence kickoff.

This means:

- use one internal ingress normalization pipeline for generic, alert, and mixed freeform requests
- normalize ingress into a set of extracted subject references rather than one flat target string
- resolve one soft primary subject plus related resources
- continue feeding the existing planner/runtime mostly one bounded execution focus at first
- allow related subjects to carry their own cluster and namespace when they are operationally relevant
- preserve generic Deployment/StatefulSet/pod investigations as first-class paths
- keep ingress domain-aware while allowing evidence gathering to become more generic and composable where possible

This does not mean:

- immediate removal of every existing wrapper or alias
- making the planner fully multi-target immediately
- collapsing all semantic targets into pods
- treating everything in one namespace as one application object
- making the host wrapper or exploratory runtime own subject meaning

## Initial Scope Constraint

The first unified-ingress slice should prefer one dominant execution scope:

- one Kubernetes context / datacenter
- one tenant namespace

If input references span multiple candidate scopes, the resolver should preserve ambiguity notes and either:

- choose one dominant scope with explicit justification
- or return a bounded scope-ambiguity outcome rather than silently merging unrelated resources

Cross-context grouped investigation is not the default problem for the first slice.
Cross-namespace related subjects are allowed when they are clearly relevant dependencies or peer-affected subjects, but they do not by themselves force full multi-target planning.

## Terminology

The model should distinguish these concepts explicitly.

### Environment Scope

- Kubernetes context / datacenter
  - execution environment such as `jed1`, `den`, `sea1`
- tenant namespace
  - a namespace such as `tenant-122346-prod`

### Logical Or Application Subjects

- Express cluster
  - one logical application cluster inside a tenant namespace
  - made up of Backend and Frontend resources only
- Express component
  - one Backend or one Frontend member of an Express cluster

### Workload Subjects

- generic workload resource
  - Deployment, StatefulSet, service, or similar Kubernetes resource
- DB workload
  - a separate workload, often a StatefulSet, that may be operationally related to an Express cluster without being semantically part of it

### Runtime Subjects

- runtime pod
  - the common runtime substrate for many workload investigations
- Kubernetes node
  - the cluster machine

Bare `node` should not remain an ambiguous internal term.
Internal terminology should reserve Kubernetes node semantics for actual cluster machines and use more explicit terms for Express members or workload components.

## Unified Ingress Model

The system should converge on one internal ingress request shape.

Illustratively:

```python
class InvestigationIngressRequest(BaseModel):
    raw_text: str | None = None
    cluster: str | None = None
    namespace: str | None = None
    source: Literal["manual", "slack", "alert_forward", "api"] = "manual"
    profile_hint: str | None = None
    structured_alerts: list[AlertFact] = Field(default_factory=list)
    explicit_refs: list[str] = Field(default_factory=list)
```

The exact type name is less important than the architectural rule:

- one internal ingress entrypoint
- many convenience wrappers may still exist temporarily
- all of them should normalize through the same subject-resolution pipeline

Existing generic and alert-specific wrappers may remain as thin compatibility aliases, but they should not own separate subject-resolution logic.

## Subject-Centric Normalization

Ingress should normalize into a subject set rather than one single target string.

Illustratively:

```python
class InvestigationSubjectRef(BaseModel):
    kind: Literal[
        "alert",
        "alert_group",
        "pod",
        "deployment",
        "statefulset",
        "backend",
        "frontend",
        "express_cluster",
        "kubernetes_node",
        "service",
        "namespace_signal",
    ]
    name: str
    cluster: str | None = None
    namespace: str | None = None
    confidence: Literal["low", "medium", "high"] = "medium"
    source_spans: list[str] = Field(default_factory=list)
```

And then:

```python
class NormalizedInvestigationSubjectSet(BaseModel):
    raw_request: str | None
    alerts: list[AlertFact]
    refs: list[InvestigationSubjectRef]
    canonical_focus: InvestigationSubjectRef | None
    related_refs: list[InvestigationSubjectRef]
    normalization_notes: list[str]
```

The key shift is:

- ingress should ask what subjects are present
- then determine what should become the soft primary focus
- rather than forcing every request into one exact operational target immediately

Alert and alert-group inputs should first normalize into subject references and scope hints.
They should not bypass subject resolution by directly becoming planner targets unless they already unambiguously identify the operational subject.
Alert groups may remain first-class subject candidates until planner-seed derivation decides whether they collapse to one dominant operational subject or require bounded ambiguity handling.

## Subject Resolution Stops Before Exact Target Collapse

Ingress is responsible for understanding:

- what the request is about
- within what scope
- which candidate subjects are present
- which related subjects are relevant
- whether scope or subject ambiguity remains bounded but unresolved

Ingress is not responsible for always deciding:

- the final exact workload target runtime will inspect first
- whether a frontend should become a service or workload investigation
- which Express component should be the first execution focus
- the final execution profile solely because an exact target was forced early

That exact collapse should happen later, in planner-seed derivation or a bounded evidence-kickoff seam, when the system can use the normalized subject set instead of pretending ingress already had perfect operational certainty.

This is an intentional architectural boundary, not an incomplete implementation.

## Soft Primary Focus And Planner Seed

The normalized subject set should preserve:

- resolved scope
- candidate subject refs
- related refs
- ambiguity notes
- one soft primary focus when one is justifiable

Illustratively, ingress may end at a shape like:

```python
class InvestigationPlannerSeed(BaseModel):
    dominant_scope: ResolvedIngressScope
    primary_subject: InvestigationSubjectRef | None
    subject_set: NormalizedInvestigationSubjectSet
    execution_target: str | None = None
    profile: str | None = None
    seed_notes: list[str] = Field(default_factory=list)
```

The exact type name is less important than the seam:

- ingress produces subject-centric meaning
- planner-seed derivation decides whether exact execution focus is already obvious
- if not, a bounded later seam narrows the focus without losing the richer subject context

Planner-seed derivation is the required semantic bridge between normalized subject sets and bounded planner/runtime execution.
It is not an optional convenience layer.

In clean cases, planner-seed derivation may be trivial:

- preserve the ingress soft primary focus
- carry forward the resolved dominant scope
- set the bounded execution focus with little or no additional narrowing

Clean cases still collapse quickly.
Messy mixed requests are allowed to remain semantically rich a little longer.

### Soft Primary Focus Versus Canonical Execution Focus

The model should distinguish these two concepts explicitly:

- soft primary focus
  - the best current semantic focal subject inferred from ingress
- canonical execution focus
  - the exact operational subject later chosen for bounded planner/runtime execution

Small practical examples:

- `express_cluster/newmetrics`
  - soft primary focus from ingress
- `backend/newmetrics-be`
  - canonical execution focus later chosen by planner-seed derivation or bounded evidence kickoff

- `service/stc1-stceei`
  - soft primary focus from an alert-shaped request
- `service/stc1-stceei`
  - canonical execution focus if the service target is already clear enough

## Canonical Focus Selection

The resolver must deterministically choose one soft primary subject from the normalized subject set when one is justifiable.

That selection should consider factors such as:

- explicitness of the reference
- specificity of the subject kind
- alert directness and severity
- namespace and context alignment
- known dependency relationships
- confidence score

If no subject is clearly dominant, the resolver should preserve ambiguity explicitly.
It should not fabricate certainty only to satisfy the current planner interface.

The normalization stage should preserve:

- competing candidates
- ambiguity notes
- why one focus won when a canonical focus is selected

## Operational Follow-On When Focus Remains Ambiguous

When no soft primary focus is justifiable, planner-seed derivation may:

- return a bounded ambiguity outcome
- request one deterministic narrowing step
- or enter a bounded evidence-kickoff seam designed to narrow focus without fabricating certainty

Planner-seed derivation remains the preferred first narrowing seam.
If it still cannot justify a bounded execution focus, a bounded scout may be used as a later fallback narrowing seam rather than the default semantic resolver.

Small practical example:

- input
  - `deployment/api and service/payments in tenant-a`
- ingress result
  - two plausible subjects, no justified soft primary focus
- next action
  - bounded ambiguity or one deterministic narrowing step, not fake certainty

When planner-seed derivation later chooses a bounded execution focus different from the soft primary focus, that divergence should be preserved explicitly in provenance and reporting.
The system should remain able to say, plainly:

- requested subject
- soft primary focus
- bounded execution focus
- why the narrowing changed

## Clarification As A Bounded Ambiguity Outcome

Some requests will remain too vague even after subject normalization and one planner-seed narrowing attempt.
In those cases, requesting clarification from the user is an allowed bounded outcome.

Clarification should be treated as:

- a last-resort ambiguity outcome
- downstream of deterministic ingress normalization
- downstream of planner-seed derivation
- a guard rail against fake certainty

Clarification should not be the default response when the system could still:

- deterministically narrow safely
- proceed with bounded ambiguity
- or use an already-approved bounded narrowing seam

The intended rule is:

- narrow automatically when the system can do so safely
- ask only when the ambiguity is decision-relevant, not cheaply resolvable, and still blocks safe bounded commitment

Illustratively, planner-seed derivation may end in:

- `ready`
  - a bounded execution focus is justified
- `needs_deterministic_narrowing`
  - one small planner-owned narrowing step may still resolve the focus
- `bounded_ambiguity`
  - the system should preserve uncertainty honestly
- `needs_clarification`
  - proceeding would likely investigate the wrong subject or scope

This ADR does not require clarification to be implemented as a resumable in-graph interrupt.
The highest-value first step is a terminal clarification outcome that returns:

- one short clarification question
- the ambiguity class
- the already-understood scope or candidate subjects when helpful

Later, if real usage justifies it, clarification may evolve into:

- resumable user-input checkpoints
- explicit pending-user-input runtime state
- or richer multi-turn clarification workflows

Those are separate implementation decisions, not part of the current ingress/planner-seed boundary change.

Small practical examples:

- input
  - `newmetrics is broken in jed1`
- likely result
  - deterministic narrowing first, because cluster is known and namespace/resource matching may still be cheap

- input
  - `prod is broken after a deploy`
- likely result
  - bounded ambiguity or clarification, because scope and subject are too vague for safe commitment

- input
  - `newmetrics or stc1-stceei may be failing in tenant-120330-prod`
- likely result
  - clarification if deterministic narrowing cannot justify which bounded execution focus should be chosen first

## Primary Subject Versus Related Resources

The correct near-term model is:

- one primary subject
- zero or more related resources
- one planner seed derived from the primary subject

This is important because several things may be operationally related without being the same semantic object.

For example:

- an Express cluster is not the same thing as its DB workload
- a tenant namespace may contain more than one Express cluster
- a namespace is usually execution scope and correlation boundary, not the target by default

So a request such as:

> newmetrics is failing, db may be involved, namespace tenant-120330-prod in jed1

should normalize more like:

- primary subject
  - `express_cluster/newmetrics`
- related resources
  - backend `newmetrics-be`
  - frontend `newmetrics-fe1`
  - frontend `newmetrics-fe2`
  - frontend `newmetrics-fe3`
  - StatefulSet `newmetrics-db` as a dependency relation

not:

- everything in the namespace is one target
- or DB is part of the Express cluster definition

In the near term, related resources are contextual attachments to one primary subject.
They should not yet imply a planner mode that treats the entire family as co-equal execution subjects.
Cross-namespace related subjects are therefore contextual first, not automatically separate execution seeds.

Related resources must not be assumed to share the dominant namespace.
The model should support related subjects with their own scope when a tenant workload depends on:

- shared platform services
- external StatefulSets or services in other namespaces
- peer-affected subjects that are operationally relevant but not semantically the same object

So the intended model is:

- one dominant scope
- one soft primary subject
- many scoped related subjects
- single-target execution for now

Dominant scope is the main execution scope for the current slice.
It is not a claim that no relevant related subjects may exist outside it.

## Intended Relation Vocabulary

The subject model should use a small intended relation vocabulary so related-subject semantics do not drift across code and docs.

Illustratively:

- `member`
  - semantically part of the same logical application family
- `dependency`
  - operationally relevant dependency, same namespace or external
- `peer_affected`
  - separate but plausibly co-affected subject
- `contextual_signal`
  - relevant signal or supporting reference that should be preserved without being treated as a likely execution focus

The exact type names may evolve, but the architecture should preserve the distinction between:

- same-family membership
- operational dependency
- peer co-affected context
- weaker contextual signal

## Related-Subject Lifecycle And Caps

Related subjects should not behave like a flat permanent bag of references.
They need a bounded lifecycle.

Illustratively, a related subject may be:

- preserved as context only
- used as contributing evidence context
- promoted into bounded execution focus by deterministic planner/runtime logic
- dropped from active context when later evidence shows it is irrelevant or stale

The architecture should also enforce caps and policy for related-subject expansion so that:

- cross-namespace dependency context does not silently become unbounded fan-out
- alert groups do not become semantic junk drawers
- related subjects do not silently become co-equal execution targets

## Ambiguity Taxonomy

Not all uncertainty is the same, and later planner/runtime behavior should not collapse them into one generic bucket.

The architecture should distinguish at least:

- scope ambiguity
- subject ambiguity
- bounded execution-focus ambiguity
- contradictory evidence
- insufficient evidence

These do not imply the same next action.
For example:

- scope ambiguity usually requires blocking or deterministic narrowing
- subject ambiguity may still preserve a soft primary focus plus competing candidates
- bounded execution-focus ambiguity may justify a bounded narrowing seam
- contradictory evidence should soften confidence rather than silently rerouting meaning
- insufficient evidence may degrade honestly without implying a different subject

## Generic Kubernetes Support Remains First-Class

The ingress refactor must not make the system Express-only.

The system must continue to support:

- generic Deployment investigations
- generic StatefulSet investigations
- direct pod investigations
- service investigations
- non-Express workloads in tenant namespaces

Express-aware resolution should therefore be:

- a specialized deterministic enrichment layer
- not the default assumption for every workload

## Resolver Layering

The preferred resolution flow is:

1. extract hard references from raw input
   - namespace
   - datacenter / context
   - pod names
   - Deployment / StatefulSet / service refs
   - Frontend / Backend refs
   - alert identifiers
2. classify references into generic versus Express-aware candidate subjects
3. enrich with deterministic Express-family grouping when the signals match Express patterns
4. choose one soft primary subject
5. attach related resources and dependency context
6. derive one current planner seed from the primary subject and subject set

This implies distinct layers such as:

- generic subject extraction
- generic subject resolution
- Express-aware enrichment and grouping
- dependency and related-resource attachment
- planner-seed derivation

## Planner Sequencing

Single ingress does not require true multi-target planning immediately.

The recommended sequence is:

### Stage 1

- single ingress model
- subject set normalization
- one soft primary subject plus related resources
- existing planner remains mostly single-focus
- planner-seed derivation may still feed one bounded execution focus when it is obvious

### Stage 2

- planner and bounded scouts may consume related-resource context as hints
- exact execution-target collapse may move from ingress into planner-seed derivation or bounded evidence kickoff where needed
- clarification may appear as a bounded terminal ambiguity outcome when deterministic narrowing still cannot safely choose a bounded execution focus

### Stage 3

- only later, if it clearly pays off, add true family-scoped or grouped-target planning modes

This keeps the semantic blast radius small while simplifying the public surface early.

## End-State Walkthroughs

These examples are illustrative, not strict wire formats.
They show the intended stage boundaries and what each stage should own.

### Example 1: Clean direct target

Input:

- `Investigate statefulset/newmetrics-db in namespace tenant-120330-prod on cluster jed1`

Expected flow:

1. ingress
   - resolves scope: `jed1`, `tenant-120330-prod`
   - extracts one subject candidate: `statefulset/newmetrics-db`
   - sets the same subject as the soft primary focus
2. planner-seed derivation
   - trivially preserves that subject as the bounded execution focus
   - produces execution target `statefulset/newmetrics-db`
3. planner/runtime
   - builds the normal single-focus workload investigation
4. reporting
   - requested subject, soft primary focus, and bounded execution focus all remain the same

### Example 2: Mixed alert/freeform request

Input:

- `EnvoyHighErrorRate is firing for stc1-stceei in tenant-120330-prod on jed1, maybe backend issue, maybe service issue`

Expected flow:

1. ingress
   - resolves scope: `jed1`, `tenant-120330-prod`
   - preserves an alert-like signal plus candidate subjects such as:
     - `express_cluster/stc1-stceei`
     - `service/stc1-stceei`
   - chooses a soft primary focus if justified, but does not force an exact workload target yet
2. planner-seed derivation
   - decides whether a bounded execution focus is already obvious
   - if not, performs one deterministic narrowing step first
   - only later falls back to a bounded scout if deterministic narrowing is insufficient
3. planner/runtime
   - may begin from `service/stc1-stceei`
   - later preserve divergence if evidence justifies narrowing to `backend/stc1-stceei-be`
4. reporting
   - must preserve:
     - requested semantic focus
     - soft primary focus
     - bounded execution focus
     - why the focus changed

### Example 3: Mixed request with dependency context

Input:

- `newmetrics is failing in tenant-120330-prod on jed1, maybe db too, and shared-cache in platform-services might be involved`

Expected flow:

1. ingress
   - resolves dominant scope: `jed1`, `tenant-120330-prod`
   - sets soft primary focus to something like `express_cluster/newmetrics`
   - preserves related subjects such as:
     - `statefulset/newmetrics-db` with relation `dependency`
     - `service/shared-cache` in namespace `platform-services` with relation `dependency`
2. planner-seed derivation
   - chooses one bounded execution focus for now, such as `backend/newmetrics-be`
   - keeps DB and cross-namespace cache context as related subjects, not co-equal execution seeds
3. runtime
   - starts from the tenant-scoped execution focus
   - only inspects the external dependency if policy and later evidence justify it
4. reporting
   - states clearly which related subjects were merely preserved as context versus which ones materially contributed evidence

### Example 4: Vague input

Input:

- `something is wrong with newmetrics in jed1`

Expected flow:

1. ingress
   - resolves cluster `jed1`
   - may still leave namespace unresolved
   - preserves low-confidence candidate subjects rather than pretending the exact target is known
2. planner-seed derivation
   - tries one deterministic narrowing step first, such as namespace/resource matching
3. outcome
   - if one bounded execution focus becomes justified, proceed normally
   - if ambiguity remains material, return bounded ambiguity or clarification instead of fabricating certainty

## Namespace And Express Semantics

The model should preserve these domain rules:

- a tenant namespace often contains one or more Express clusters plus DB workloads and possibly other workloads
- an Express cluster itself is only Backend plus Frontend resources and their pods
- DB workloads are separate workload subjects even when they are operationally related
- namespace is usually context, search scope, and correlation boundary rather than the target

## Pod As Runtime Layer, Not Identity Layer

Pods are the shared runtime substrate for many workload types.

That means:

- evidence often converges on pod state, logs, events, and container facts

Pods are the most common runtime evidence object, but they are not the only valid canonical subject.
The resolver may derive pod execution targets from many subject kinds, but subject identity should remain at the most useful semantic level available.

But the semantic subject may still be:

- an Express cluster
- an Express component
- a Deployment
- a StatefulSet
- a DB workload
- a service
- a pod
- a Kubernetes node

So the internal model should distinguish:

- semantic subject
- execution target
- runtime evidence substrate

## Domain-Aware Ingress, Generic Evidence Planes

Ingress should remain domain-aware:

- tenant namespace matters
- Express grouping matters when signals fit
- DB workloads are related but not semantically part of an Express cluster
- related subjects may cross namespace boundaries

Evidence gathering should become more generic and composable where possible:

- get resource
- describe resource
- get events
- get logs
- get metrics
- list related runtime objects

The architecture should therefore split responsibility cleanly:

- ingress owns meaning
- planner-seed derivation owns bounded commitment to execution focus
- evidence planes gather data generically where possible
- runtime remains deterministic and typed

Generic evidence planes must not become shadow semantic resolvers.
They may gather data broadly, but they should not quietly re-own subject meaning, family grouping, or dependency semantics outside typed planner-owned contracts.

## Non-Goals For This ADR Revision

This revision does not require:

- true multi-target planning yet
- family-scoped parallel execution
- collapsing all investigations to pods
- making namespace the only investigation boundary
- moving semantic meaning into the host wrapper or exploratory runtime
- preserving internal helpers whose only purpose is eager exact-target collapse

## Non-Goals For This Slice

This ADR does not imply:

- cross-context multi-subject investigation planning
- cross-namespace family planning by default
- making every related resource a co-equal execution subject
- replacing all public wrappers immediately
- redesigning all planner semantics at the same time as ingress

## Failure Modes This Direction Intends To Avoid

This direction is intentionally meant to avoid:

- fake certainty from early exact-target collapse
- accidental namespace or object-family conflation
- semantic meaning being re-invented ad hoc in runtime or host layers
- reports that hide when bounded execution focus diverged from the earlier semantic framing

## Consequences

### Positive

- one simpler internal front door for generic, alert, and mixed freeform investigation input
- cleaner handling of multiple related references in the same request
- preserves current planner/runtime stability by keeping near-term planning mostly single-focus
- leaves room for later family-scoped or multi-target planning without forcing it now
- keeps generic Kubernetes workloads first-class while still supporting Express-specific semantics

### Negative

- adds a new normalization and subject-resolution layer to maintain
- requires careful terminology cleanup around cluster, node, namespace, and Express terms
- risks false certainty if confidence and ambiguity are not preserved explicitly during normalization
- requires planner-seed derivation to become a real seam rather than letting random helpers recreate eager collapse elsewhere
- requires reports and operator-facing outputs to explain focus changes truthfully when bounded execution focus differs from the earlier semantic focus

## Implementation Direction

The preferred implementation order is:

1. add a unified internal ingress request model
2. add subject-reference and normalized-subject-set models
3. add deterministic generic extraction and resolution
4. add deterministic Express-aware grouping and dependency attachment
5. derive current planner seed inputs from the primary subject while preserving related resources as context
6. keep public wrappers as thin aliases until the unified ingress path proves reliable
7. only later consider true multi-target or family-scoped planning modes

As planner-seed derivation becomes the preferred seam, ingress-local helpers whose main job is eager exact-target collapse, profile promotion, or CR-backed operational rewriting are candidates for simplification or removal once the planner-seed behavior is proven.

## What This ADR Does Not Decide

This ADR does not decide:

- the exact final public API shape
- whether current alert and generic wrappers should disappear immediately
- whether multi-target planning should be added soon
- exact family/group planning modes
- exact UI or report rendering for related-resource context
- the exact future resume/checkpoint protocol for clarification turns

The narrower decision is:

> unify the front door around subject-centric normalization now, while keeping the inner planner mostly single-focus for one more phase.
