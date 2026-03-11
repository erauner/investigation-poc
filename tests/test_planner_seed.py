from investigation_service.ingress import subject_context_from_subject_set
from investigation_service.models import InvestigationIngressRequest, InvestigationSubjectRef, NormalizedInvestigationSubjectSet, ResolvedIngressScope
from investigation_service.planner_seed import (
    PlannerSeedDeps,
    normalized_request_from_planner_seed,
    planner_seed_from_subject_set,
)


def _deps(**overrides) -> PlannerSeedDeps:
    base = PlannerSeedDeps(
        canonical_target=lambda target, profile, service_name: f"deployment/{target}",
        scope_from_target=lambda target, profile: "workload",
        resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": cluster})(),
        get_backend_cr=lambda namespace, name, cluster=None: {},
        get_frontend_cr=lambda namespace, name, cluster=None: {},
        get_cluster_cr=lambda namespace, name, cluster=None: {},
    )
    return PlannerSeedDeps(**{**base.__dict__, **overrides})


def _subject_set(
    *,
    cluster: str | None = "erauner-home",
    namespace: str | None = "operator-smoke",
    focus: InvestigationSubjectRef | None = None,
    candidates: list[InvestigationSubjectRef] | None = None,
    ambiguous_clusters: list[str] | None = None,
    ambiguous_namespaces: list[str] | None = None,
) -> NormalizedInvestigationSubjectSet:
    if focus is None and candidates is None:
        focus = InvestigationSubjectRef(
            kind="statefulset",
            name="crashy-db",
            cluster=cluster,
            namespace=namespace,
            confidence="high",
            sources=["explicit_target"],
        )
    return NormalizedInvestigationSubjectSet(
        ingress=InvestigationIngressRequest(
            source="manual",
            cluster=cluster,
            namespace=namespace,
            target="statefulset/crashy-db" if focus else None,
            profile_hint="workload",
        ),
        scope=ResolvedIngressScope(
            cluster=cluster,
            namespace=namespace,
            cluster_source="explicit" if cluster is not None else "none",
            namespace_source="explicit" if namespace is not None else "none",
            ambiguous_clusters=ambiguous_clusters or [],
            ambiguous_namespaces=ambiguous_namespaces or [],
        ),
        candidate_refs=candidates or ([focus] if focus is not None else []),
        canonical_focus=focus,
        related_refs=[],
        normalization_notes=[],
    )


def test_planner_seed_resolves_direct_statefulset_focus() -> None:
    subject_set = _subject_set()
    seed = planner_seed_from_subject_set(
        subject_set,
        subject_context=subject_context_from_subject_set(subject_set),
        deps=_deps(),
    )

    assert seed.outcome == "execution_focus_resolved"
    assert seed.execution_focus is not None
    assert seed.execution_focus.target == "statefulset/crashy-db"


def test_planner_seed_marks_ambiguous_scope_without_execution_focus() -> None:
    subject_set = _subject_set(ambiguous_namespaces=["tenant-a", "tenant-b"])
    seed = planner_seed_from_subject_set(
        subject_set,
        subject_context=subject_context_from_subject_set(subject_set),
        deps=_deps(),
    )

    assert seed.outcome == "bounded_ambiguity"
    assert seed.execution_focus is None


def test_normalized_request_from_planner_seed_replays_ambiguity_error() -> None:
    subject_set = _subject_set(ambiguous_clusters=["jed1", "hnd1"])
    seed = planner_seed_from_subject_set(
        subject_set,
        subject_context=subject_context_from_subject_set(subject_set),
        deps=_deps(),
    )

    try:
        normalized_request_from_planner_seed(seed)
    except ValueError as exc:
        assert str(exc) == "bounded ingress ambiguity: cluster scope candidates=jed1, hnd1"
    else:  # pragma: no cover - defensive
        raise AssertionError("expected bounded ambiguity error")


def test_planner_seed_requested_target_uses_primary_subject_when_no_explicit_target() -> None:
    focus = InvestigationSubjectRef(
        kind="service",
        name="checkout",
        cluster="erauner-home",
        namespace="operator-smoke",
        confidence="medium",
        sources=["question_text"],
    )
    subject_set = _subject_set(focus=focus)
    subject_set = subject_set.model_copy(
        update={
            "ingress": subject_set.ingress.model_copy(update={"target": None}),
            "candidate_refs": [focus],
        }
    )

    seed = planner_seed_from_subject_set(
        subject_set,
        subject_context=subject_context_from_subject_set(subject_set),
        deps=_deps(),
    )

    assert seed.requested_target == "service/checkout"


def test_planner_seed_raises_when_cluster_lookup_fails() -> None:
    focus = InvestigationSubjectRef(
        kind="express_cluster",
        name="tenant",
        cluster="erauner-home",
        namespace="operator-smoke",
        confidence="high",
        sources=["explicit_target"],
    )
    subject_set = _subject_set(focus=focus)

    try:
        planner_seed_from_subject_set(
            subject_set,
            subject_context=subject_context_from_subject_set(subject_set),
            deps=_deps(get_cluster_cr=lambda namespace, name, cluster=None: {"error": "not found"}),
        )
    except ValueError as exc:
        assert str(exc) == "cluster lookup failed for tenant: not found"
    else:  # pragma: no cover - defensive
        raise AssertionError("expected cluster lookup failure")
