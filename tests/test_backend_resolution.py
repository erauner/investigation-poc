from investigation_service.ingress import subject_context_from_subject_set
from investigation_service.models import (
    InvestigationIngressRequest,
    InvestigationSubjectRef,
    NormalizedInvestigationSubjectSet,
    ResolvedIngressScope,
)
from investigation_service.planner_seed import (
    PlannerSeedDeps,
    normalized_request_from_planner_seed,
    planner_seed_from_subject_set,
)


def _deps(**overrides) -> PlannerSeedDeps:
    base = PlannerSeedDeps(
        canonical_target=lambda target, profile, service_name: target,
        scope_from_target=lambda target, profile: "workload",
        resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": cluster})(),
        get_backend_cr=lambda namespace, name, cluster=None: {},
        get_frontend_cr=lambda namespace, name, cluster=None: {},
        get_cluster_cr=lambda namespace, name, cluster=None: {},
    )
    return PlannerSeedDeps(**{**base.__dict__, **overrides})


def _normalized_from_target(
    *,
    cluster: str | None,
    namespace: str,
    target: str,
    profile: str = "workload",
    deps: PlannerSeedDeps,
):
    kind, name = target.split("/", 1)
    normalized_kind = {
        "Backend": "backend",
        "Frontend": "frontend",
        "Cluster": "express_cluster",
    }.get(kind, kind.lower())
    subject_set = NormalizedInvestigationSubjectSet(
        ingress=InvestigationIngressRequest(
            source="manual",
            cluster=cluster,
            namespace=namespace,
            target=target,
            profile_hint=profile,  # type: ignore[arg-type]
        ),
        scope=ResolvedIngressScope(
            cluster=cluster,
            namespace=namespace,
            cluster_source="explicit",
            namespace_source="explicit",
        ),
        candidate_refs=[
            InvestigationSubjectRef(
                kind=normalized_kind,  # type: ignore[arg-type]
                name=name,
                cluster=cluster,
                namespace=namespace,
                confidence="high",
                sources=["explicit_target"],
            )
        ],
        canonical_focus=InvestigationSubjectRef(
            kind=normalized_kind,  # type: ignore[arg-type]
            name=name,
            cluster=cluster,
            namespace=namespace,
            confidence="high",
            sources=["explicit_target"],
        ),
        related_refs=[],
        normalization_notes=[],
    )
    seed = planner_seed_from_subject_set(
        subject_set,
        subject_context=subject_context_from_subject_set(subject_set),
        deps=deps,
    )
    return normalized_request_from_planner_seed(seed)


def test_backend_resolution_notes_fallback_when_backend_lookup_fails() -> None:
    deps = _deps(
        get_backend_cr=lambda namespace, name, cluster=None: {
            "error": "not found",
            "namespace": namespace,
            "name": name,
        }
    )

    normalized = _normalized_from_target(
        cluster="erauner-home",
        namespace="operator-smoke",
        target="Backend/crashy",
        deps=deps,
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.target == "deployment/crashy"
    assert normalized.service_name == "crashy"
    assert "resolved Backend/crashy to deployment/crashy" in normalized.normalization_notes
    assert "backend lookup failed; using deployment fallback" in normalized.normalization_notes


def test_frontend_resolution_notes_fallback_when_frontend_lookup_fails() -> None:
    deps = _deps(
        get_frontend_cr=lambda namespace, name, cluster=None: {
            "error": "not found",
            "namespace": namespace,
            "name": name,
        }
    )

    normalized = _normalized_from_target(
        cluster="erauner-home",
        namespace="operator-smoke",
        target="Frontend/landing",
        deps=deps,
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.target == "deployment/landing"
    assert normalized.service_name == "landing"
    assert "resolved Frontend/landing to deployment/landing" in normalized.normalization_notes
    assert "frontend lookup failed; using deployment/landing fallback" in normalized.normalization_notes


def test_explicit_frontend_target_stays_workload_scope_even_with_service_profile() -> None:
    deps = _deps(
        get_frontend_cr=lambda namespace, name, cluster=None: {
            "error": "not found",
            "namespace": namespace,
            "name": name,
        }
    )

    normalized = _normalized_from_target(
        cluster="erauner-home",
        namespace="operator-smoke",
        target="Frontend/landing",
        profile="service",
        deps=deps,
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.scope == "workload"
    assert normalized.profile == "service"
    assert normalized.service_name == "landing"
    assert normalized.target == "deployment/landing"
    assert "resolved Frontend/landing to deployment/landing" in normalized.normalization_notes
    assert "frontend lookup failed; using deployment/landing fallback" in normalized.normalization_notes


def test_cluster_resolution_picks_first_failing_component() -> None:
    deps = _deps(
        get_cluster_cr=lambda namespace, name, cluster=None: {
            "status": {
                "componentStatuses": [
                    {"name": "landing", "kind": "Frontend", "wave": 2, "phase": "Healthy", "ready": True},
                    {"name": "api", "kind": "Backend", "wave": 1, "phase": "Failed", "ready": False},
                ]
            }
        }
    )

    normalized = _normalized_from_target(
        cluster="erauner-home",
        namespace="operator-smoke",
        target="Cluster/testapp",
        deps=deps,
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.target == "deployment/api"
    assert normalized.service_name == "api"
    assert "resolved Cluster/testapp to failing component Backend/api" in normalized.normalization_notes
    assert "resolved Backend/api to deployment/api" in normalized.normalization_notes


def test_cluster_service_profile_resolves_frontend_component_to_service() -> None:
    deps = _deps(
        get_cluster_cr=lambda namespace, name, cluster=None: {
            "status": {
                "componentStatuses": [
                    {"name": "landing", "kind": "Frontend", "wave": 2, "phase": "Failed", "ready": False},
                    {"name": "api", "kind": "Backend", "wave": 1, "phase": "Healthy", "ready": True},
                ]
            }
        }
    )

    normalized = _normalized_from_target(
        cluster="erauner-home",
        namespace="operator-smoke",
        target="Cluster/testapp",
        profile="service",
        deps=deps,
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.scope == "service"
    assert normalized.profile == "service"
    assert normalized.service_name == "landing"
    assert normalized.target == "service/landing"
    assert "resolved Cluster/testapp to failing component Frontend/landing" in normalized.normalization_notes
    assert "resolved Frontend/landing to service/landing" in normalized.normalization_notes


def test_cluster_resolution_preserves_statefulset_component_target() -> None:
    deps = _deps(
        get_cluster_cr=lambda namespace, name, cluster=None: {
            "status": {
                "componentStatuses": [
                    {"name": "newmetrics-db", "kind": "StatefulSet", "wave": 1, "phase": "Failed", "ready": False},
                    {"name": "api", "kind": "Backend", "wave": 2, "phase": "Healthy", "ready": True},
                ]
            }
        }
    )

    normalized = _normalized_from_target(
        cluster="erauner-home",
        namespace="operator-smoke",
        target="Cluster/testapp",
        deps=deps,
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.target == "statefulset/newmetrics-db"
    assert "resolved Cluster/testapp to failing component StatefulSet/newmetrics-db" in normalized.normalization_notes
    assert "resolved StatefulSet/newmetrics-db to statefulset/newmetrics-db" in normalized.normalization_notes


def test_backend_explicit_current_context_resolves_in_legacy_mode() -> None:
    deps = _deps(
        resolve_cluster=lambda cluster: type(
            "ResolvedCluster",
            (),
            {"alias": "current-context", "source": "legacy_current_context"},
        )(),
        get_backend_cr=lambda namespace, name, cluster=None: {
            "kind": "Backend",
            "metadata": {"name": name, "namespace": namespace},
        },
    )

    normalized = _normalized_from_target(
        cluster=None,
        namespace="operator-smoke",
        target="Backend/crashy",
        deps=deps,
    )

    assert normalized.cluster is None
    assert normalized.target == "deployment/crashy"
    assert normalized.service_name == "crashy"
