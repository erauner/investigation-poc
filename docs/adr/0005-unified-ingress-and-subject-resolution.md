# ADR 0005: Unify Investigation Ingress Around Subject Resolution

- Status: Proposed
- Date: 2026-03-11
- Related ADRs:
  - `docs/adr/0001-artifact-oriented-investigation-workflow.md`
  - `docs/adr/0003-langgraph-execution-shell.md`
  - `docs/adr/0004-bounded-exploratory-evidence.md`

## Context

The planner-led runtime is now strong once it starts from:

- one resolved canonical focus
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

> unify ingress and subject resolution first, keep planning mostly single-focus internally for now, and defer true multi-target planning until it is clearly worth the added semantic complexity.

This means:

- use one internal ingress normalization pipeline for generic, alert, and mixed freeform requests
- normalize ingress into a set of extracted subject references rather than one flat target string
- resolve one canonical primary subject plus related resources
- continue feeding the existing planner/runtime mostly one canonical focus at first
- preserve generic Deployment/StatefulSet/pod investigations as first-class paths

This does not mean:

- immediate removal of every existing wrapper or alias
- making the planner fully multi-target immediately
- collapsing all semantic targets into pods
- treating everything in one namespace as one application object

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
- then determine what should become the canonical operational focus
- rather than forcing every request into one target string immediately

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
4. choose one canonical primary subject
5. attach related resources and dependency context
6. derive one current planner seed from the primary subject

This implies distinct layers such as:

- generic subject extraction
- generic subject resolution
- Express-aware enrichment and grouping
- dependency and related-resource attachment

## Planner Sequencing

Single ingress does not require true multi-target planning immediately.

The recommended sequence is:

### Stage 1

- single ingress model
- subject set normalization
- one primary subject plus related resources
- existing planner remains mostly single-focus

### Stage 2

- planner and bounded scouts may consume related-resource context as hints

### Stage 3

- only later, if it clearly pays off, add true family-scoped or grouped-target planning modes

This keeps the semantic blast radius small while simplifying the public surface early.

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
- runtime evidence

rather than collapsing everything into pods.

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

## Implementation Direction

The preferred implementation order is:

1. add a unified internal ingress request model
2. add subject-reference and normalized-subject-set models
3. add deterministic generic extraction and resolution
4. add deterministic Express-aware grouping and dependency attachment
5. derive current planner seed inputs from the primary subject while preserving related resources as context
6. keep public wrappers as thin aliases until the unified ingress path proves reliable
7. only later consider true multi-target or family-scoped planning modes

## What This ADR Does Not Decide

This ADR does not decide:

- the exact final public API shape
- whether current alert and generic wrappers should disappear immediately
- whether multi-target planning should be added soon
- exact family/group planning modes
- exact UI or report rendering for related-resource context

The narrower decision is:

> unify the front door around subject-centric normalization now, while keeping the inner planner mostly single-focus for one more phase.
