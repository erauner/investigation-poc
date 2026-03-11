from investigation_service.ingress import IngressDeps, normalize_ingress_request, subject_context_from_subject_set
from investigation_service.models import InvestigationIngressRequest, InvestigationSubjectRef, NormalizedInvestigationSubjectSet, ResolvedIngressScope
from investigation_service.planner_seed import (
    PostSeedNormalizationDeps,
    apply_post_seed_normalization,
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


def test_normalize_ingress_request_emits_resource_hint_for_vague_workload_text() -> None:
    subject_set = normalize_ingress_request(
        InvestigationIngressRequest(
            source="manual",
            namespace="default",
            question="Investigate unhealthy workload in namespace default",
            raw_text="Investigate unhealthy workload in namespace default",
            profile_hint="workload",
        ),
        IngressDeps(
            resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": cluster})(),
            get_cluster_cr=lambda namespace, name, cluster=None: {},
        ),
    )

    assert [(ref.kind, ref.name, ref.sources) for ref in subject_set.candidate_refs] == [
        ("resource_hint", "workload", ["vague_workload"])
    ]
    assert subject_set.canonical_focus is not None
    assert subject_set.canonical_focus.kind == "resource_hint"
    assert subject_set.canonical_focus.name == "workload"
    assert all("resolved vague workload target" not in note for note in subject_set.normalization_notes)


def test_vague_workload_hint_only_becomes_concrete_in_post_seed_normalization() -> None:
    focus = InvestigationSubjectRef(
        kind="resource_hint",
        name="workload",
        cluster="erauner-home",
        namespace="default",
        confidence="medium",
        sources=["vague_workload"],
    )
    subject_set = _subject_set(focus=focus)
    subject_set = subject_set.model_copy(
        update={
            "ingress": subject_set.ingress.model_copy(update={"target": None, "question": "Investigate unhealthy workload"}),
            "candidate_refs": [focus],
        }
    )

    seed = planner_seed_from_subject_set(
        subject_set,
        subject_context=subject_context_from_subject_set(subject_set),
        deps=_deps(canonical_target=lambda target, profile, service_name: target),
    )

    assert seed.execution_focus is not None
    assert seed.execution_focus.target == "workload"

    normalized = normalized_request_from_planner_seed(seed)
    assert normalized.target == "workload"

    concretized = apply_post_seed_normalization(
        normalized,
        PostSeedNormalizationDeps(
            find_unhealthy_pod=lambda req: type(
                "UnhealthyPodResponse",
                (),
                {"candidate": type("Candidate", (), {"target": "pod/crashy-abc123"})()},
            )()
        ),
    )

    assert concretized.target == "pod/crashy-abc123"
    assert "resolved vague workload target to pod/crashy-abc123" in concretized.normalization_notes
