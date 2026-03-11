from investigation_service import planner
from investigation_service.models import NormalizedInvestigationRequest
from investigation_service.planner import PlannerDeps


def _deps(**overrides) -> PlannerDeps:
    base = PlannerDeps(
        normalize_alert_input=lambda req: (_ for _ in ()).throw(AssertionError(f"unexpected alert normalization: {req}")),
        canonical_target=lambda target, profile, service_name: target,
        scope_from_target=lambda target, profile: "workload",
        resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": cluster})(),
        get_backend_cr=lambda namespace, name, cluster=None: {},
        get_frontend_cr=lambda namespace, name, cluster=None: {},
        get_cluster_cr=lambda namespace, name, cluster=None: {},
        find_unhealthy_pod=lambda req: None,
    )
    return PlannerDeps(**{**base.__dict__, **overrides})

def _seed_normalized(
    *,
    cluster: str | None,
    namespace: str,
    target: str,
    profile: str = "workload",
) -> NormalizedInvestigationRequest:
    return NormalizedInvestigationRequest(
        source="manual",
        scope="workload" if profile != "service" else "service",
        cluster=cluster,
        namespace=namespace,
        target=target,
        profile=profile,
        normalization_notes=[],
    )


def test_backend_resolution_notes_fallback_when_backend_lookup_fails() -> None:
    deps = _deps(
        resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": "erauner-home"})(),
        get_backend_cr=lambda namespace, name, cluster=None: {"error": "not found", "namespace": namespace, "name": name},
    )

    normalized = planner.resolve_backend_convenience_target(
        _seed_normalized(cluster="erauner-home", namespace="operator-smoke", target="Backend/crashy"),
        deps,
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.target == "deployment/crashy"
    assert normalized.service_name == "crashy"
    assert "resolved Backend/crashy to deployment/crashy" in normalized.normalization_notes
    assert "backend lookup failed; using deployment fallback" in normalized.normalization_notes


def test_frontend_resolution_notes_fallback_when_frontend_lookup_fails() -> None:
    deps = _deps(
        resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": "erauner-home"})(),
        get_frontend_cr=lambda namespace, name, cluster=None: {"error": "not found", "namespace": namespace, "name": name},
    )

    normalized = planner.resolve_frontend_convenience_target(
        _seed_normalized(cluster="erauner-home", namespace="operator-smoke", target="Frontend/landing"),
        deps,
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.target == "deployment/landing"
    assert normalized.service_name == "landing"
    assert "resolved Frontend/landing to deployment/landing" in normalized.normalization_notes
    assert "frontend lookup failed; using deployment/landing fallback" in normalized.normalization_notes


def test_frontend_service_profile_resolves_to_service_when_lookup_fails() -> None:
    deps = _deps(
        resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": "erauner-home"})(),
        get_frontend_cr=lambda namespace, name, cluster=None: {"error": "not found", "namespace": namespace, "name": name},
    )

    normalized = planner.resolve_frontend_convenience_target(
        _seed_normalized(
            cluster="erauner-home",
            namespace="operator-smoke",
            target="Frontend/landing",
            profile="service",
        ),
        deps,
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.scope == "service"
    assert normalized.profile == "service"
    assert normalized.service_name == "landing"
    assert normalized.target == "service/landing"
    assert "resolved Frontend/landing to service/landing" in normalized.normalization_notes
    assert "frontend lookup failed; using service/landing fallback" in normalized.normalization_notes


def test_frontend_legacy_current_context_does_not_set_cluster_alias() -> None:
    deps = _deps(
        resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": "current-context", "source": "legacy_current_context"})(),
        get_frontend_cr=lambda namespace, name, cluster=None: {"kind": "Frontend", "metadata": {"name": name, "namespace": namespace}},
    )

    normalized = planner.resolve_frontend_convenience_target(
        _seed_normalized(cluster=None, namespace="operator-smoke", target="Frontend/landing", profile="service"),
        deps,
    )

    assert normalized.cluster is None
    assert normalized.target == "service/landing"


def test_cluster_resolution_picks_first_failing_component() -> None:
    deps = _deps(
        resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": "erauner-home"})(),
        get_cluster_cr=lambda namespace, name, cluster=None: {
            "status": {
                "componentStatuses": [
                    {"name": "landing", "kind": "Frontend", "wave": 2, "phase": "Healthy", "ready": True},
                    {"name": "api", "kind": "Backend", "wave": 1, "phase": "Failed", "ready": False},
                ]
            }
        },
    )

    normalized = planner.resolve_cluster_convenience_target(
        _seed_normalized(cluster="erauner-home", namespace="operator-smoke", target="Cluster/testapp"),
        deps,
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.target == "deployment/api"
    assert normalized.service_name == "api"
    assert "resolved Cluster/testapp to failing component Backend/api" in normalized.normalization_notes
    assert "resolved Backend/api to deployment/api" in normalized.normalization_notes


def test_cluster_service_profile_resolves_frontend_component_to_service() -> None:
    deps = _deps(
        resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": "erauner-home"})(),
        get_cluster_cr=lambda namespace, name, cluster=None: {
            "status": {
                "componentStatuses": [
                    {"name": "landing", "kind": "Frontend", "wave": 2, "phase": "Failed", "ready": False},
                    {"name": "api", "kind": "Backend", "wave": 1, "phase": "Healthy", "ready": True},
                ]
            }
        },
    )

    normalized = planner.resolve_cluster_convenience_target(
        _seed_normalized(
            cluster="erauner-home",
            namespace="operator-smoke",
            target="Cluster/testapp",
            profile="service",
        ),
        deps,
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
        resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": "erauner-home"})(),
        get_cluster_cr=lambda namespace, name, cluster=None: {
            "status": {
                "componentStatuses": [
                    {"name": "newmetrics-db", "kind": "StatefulSet", "wave": 1, "phase": "Failed", "ready": False},
                    {"name": "api", "kind": "Backend", "wave": 2, "phase": "Healthy", "ready": True},
                ]
            }
        },
    )

    normalized = planner.resolve_cluster_convenience_target(
        _seed_normalized(cluster="erauner-home", namespace="operator-smoke", target="Cluster/testapp"),
        deps,
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.target == "statefulset/newmetrics-db"
    assert "resolved Cluster/testapp to failing component StatefulSet/newmetrics-db" in normalized.normalization_notes
    assert "resolved StatefulSet/newmetrics-db to statefulset/newmetrics-db" in normalized.normalization_notes


def test_cluster_legacy_current_context_does_not_set_cluster_alias() -> None:
    deps = _deps(
        resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": "current-context", "source": "legacy_current_context"})(),
        get_cluster_cr=lambda namespace, name, cluster=None: {
            "status": {
                "componentStatuses": [
                    {"name": "landing", "kind": "Frontend", "wave": 1, "phase": "Healthy", "ready": True},
                ]
            }
        },
    )

    normalized = planner.resolve_cluster_convenience_target(
        _seed_normalized(cluster=None, namespace="operator-smoke", target="Cluster/testapp", profile="service"),
        deps,
    )

    assert normalized.cluster is None
    assert normalized.target == "service/landing"


def test_backend_explicit_current_context_resolves_in_legacy_mode() -> None:
    deps = _deps(
        resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": "current-context", "source": "legacy_current_context"})(),
        get_backend_cr=lambda namespace, name, cluster=None: {"kind": "Backend", "metadata": {"name": name, "namespace": namespace}},
    )

    normalized = planner.resolve_backend_convenience_target(
        _seed_normalized(cluster="current-context", namespace="operator-smoke", target="Backend/crashy"),
        deps,
    )

    assert normalized.cluster is None
    assert normalized.target == "deployment/crashy"
    assert normalized.service_name == "crashy"
