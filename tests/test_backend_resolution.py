from investigation_service.models import InvestigationReportRequest
from investigation_service import reporting


def test_backend_resolution_notes_fallback_when_backend_lookup_fails(monkeypatch) -> None:
    resolved_cluster = type("ResolvedCluster", (), {"alias": "erauner-home"})()
    monkeypatch.setattr(reporting, "resolve_cluster", lambda cluster: resolved_cluster)
    monkeypatch.setattr(
        reporting,
        "get_backend_cr",
        lambda namespace, name, cluster=None: {"error": "not found", "namespace": namespace, "name": name},
    )

    normalized = reporting._resolve_backend_convenience_target(
        reporting._normalized_request(
            InvestigationReportRequest(
                cluster="erauner-home",
                namespace="operator-smoke",
                target="Backend/crashy",
                include_related_data=False,
            )
        )
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.target == "deployment/crashy"
    assert normalized.service_name == "crashy"
    assert "resolved Backend/crashy to deployment/crashy" in normalized.normalization_notes
    assert "backend lookup failed; using deployment fallback" in normalized.normalization_notes


def test_frontend_resolution_notes_fallback_when_frontend_lookup_fails(monkeypatch) -> None:
    resolved_cluster = type("ResolvedCluster", (), {"alias": "erauner-home"})()
    monkeypatch.setattr(reporting, "resolve_cluster", lambda cluster: resolved_cluster)
    monkeypatch.setattr(
        reporting,
        "get_frontend_cr",
        lambda namespace, name, cluster=None: {"error": "not found", "namespace": namespace, "name": name},
    )

    normalized = reporting._resolve_frontend_convenience_target(
        reporting._normalized_request(
            InvestigationReportRequest(
                cluster="erauner-home",
                namespace="operator-smoke",
                target="Frontend/landing",
                include_related_data=False,
            )
        )
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.target == "deployment/landing"
    assert normalized.service_name == "landing"
    assert "resolved Frontend/landing to deployment/landing" in normalized.normalization_notes
    assert "frontend lookup failed; using deployment/landing fallback" in normalized.normalization_notes


def test_frontend_service_profile_resolves_to_service_when_lookup_fails(monkeypatch) -> None:
    resolved_cluster = type("ResolvedCluster", (), {"alias": "erauner-home"})()
    monkeypatch.setattr(reporting, "resolve_cluster", lambda cluster: resolved_cluster)
    monkeypatch.setattr(
        reporting,
        "get_frontend_cr",
        lambda namespace, name, cluster=None: {"error": "not found", "namespace": namespace, "name": name},
    )

    normalized = reporting._resolve_frontend_convenience_target(
        reporting._normalized_request(
            InvestigationReportRequest(
                cluster="erauner-home",
                namespace="operator-smoke",
                target="Frontend/landing",
                profile="service",
                include_related_data=False,
            )
        )
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.scope == "service"
    assert normalized.profile == "service"
    assert normalized.service_name == "landing"
    assert normalized.target == "service/landing"
    assert "resolved Frontend/landing to service/landing" in normalized.normalization_notes
    assert "frontend lookup failed; using service/landing fallback" in normalized.normalization_notes


def test_frontend_legacy_current_context_does_not_set_cluster_alias(monkeypatch) -> None:
    resolved_cluster = type("ResolvedCluster", (), {"alias": "current-context", "source": "legacy_current_context"})()
    monkeypatch.setattr(reporting, "resolve_cluster", lambda cluster: resolved_cluster)
    monkeypatch.setattr(
        reporting,
        "get_frontend_cr",
        lambda namespace, name, cluster=None: {"kind": "Frontend", "metadata": {"name": name, "namespace": namespace}},
    )

    normalized = reporting._resolve_frontend_convenience_target(
        reporting._normalized_request(
            InvestigationReportRequest(
                namespace="operator-smoke",
                target="Frontend/landing",
                profile="service",
                include_related_data=False,
            )
        )
    )

    assert normalized.cluster is None
    assert normalized.target == "service/landing"


def test_cluster_resolution_picks_first_failing_component(monkeypatch) -> None:
    resolved_cluster = type("ResolvedCluster", (), {"alias": "erauner-home"})()
    monkeypatch.setattr(reporting, "resolve_cluster", lambda cluster: resolved_cluster)
    monkeypatch.setattr(
        reporting,
        "get_cluster_cr",
        lambda namespace, name, cluster=None: {
            "status": {
                "componentStatuses": [
                    {"name": "landing", "kind": "Frontend", "wave": 2, "phase": "Healthy", "ready": True},
                    {"name": "api", "kind": "Backend", "wave": 1, "phase": "Failed", "ready": False},
                ]
            }
        },
    )

    normalized = reporting._resolve_cluster_convenience_target(
        reporting._normalized_request(
            InvestigationReportRequest(
                cluster="erauner-home",
                namespace="operator-smoke",
                target="Cluster/testapp",
                include_related_data=False,
            )
        )
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.target == "deployment/api"
    assert normalized.service_name == "api"
    assert "resolved Cluster/testapp to failing component Backend/api" in normalized.normalization_notes
    assert "resolved Backend/api to deployment/api" in normalized.normalization_notes


def test_cluster_service_profile_resolves_frontend_component_to_service(monkeypatch) -> None:
    resolved_cluster = type("ResolvedCluster", (), {"alias": "erauner-home"})()
    monkeypatch.setattr(reporting, "resolve_cluster", lambda cluster: resolved_cluster)
    monkeypatch.setattr(
        reporting,
        "get_cluster_cr",
        lambda namespace, name, cluster=None: {
            "status": {
                "componentStatuses": [
                    {"name": "landing", "kind": "Frontend", "wave": 2, "phase": "Failed", "ready": False},
                    {"name": "api", "kind": "Backend", "wave": 1, "phase": "Healthy", "ready": True},
                ]
            }
        },
    )

    normalized = reporting._resolve_cluster_convenience_target(
        reporting._normalized_request(
            InvestigationReportRequest(
                cluster="erauner-home",
                namespace="operator-smoke",
                target="Cluster/testapp",
                profile="service",
                include_related_data=False,
            )
        )
    )

    assert normalized.cluster == "erauner-home"
    assert normalized.scope == "service"
    assert normalized.profile == "service"
    assert normalized.service_name == "landing"
    assert normalized.target == "service/landing"
    assert "resolved Cluster/testapp to failing component Frontend/landing" in normalized.normalization_notes
    assert "resolved Frontend/landing to service/landing" in normalized.normalization_notes


def test_cluster_legacy_current_context_does_not_set_cluster_alias(monkeypatch) -> None:
    resolved_cluster = type("ResolvedCluster", (), {"alias": "current-context", "source": "legacy_current_context"})()
    monkeypatch.setattr(reporting, "resolve_cluster", lambda cluster: resolved_cluster)
    monkeypatch.setattr(
        reporting,
        "get_cluster_cr",
        lambda namespace, name, cluster=None: {
            "status": {
                "componentStatuses": [
                    {"name": "landing", "kind": "Frontend", "wave": 1, "phase": "Healthy", "ready": True},
                ]
            }
        },
    )

    normalized = reporting._resolve_cluster_convenience_target(
        reporting._normalized_request(
            InvestigationReportRequest(
                namespace="operator-smoke",
                target="Cluster/testapp",
                profile="service",
                include_related_data=False,
            )
        )
    )

    assert normalized.cluster is None
    assert normalized.target == "service/landing"


def test_backend_explicit_current_context_resolves_in_legacy_mode(monkeypatch) -> None:
    monkeypatch.delenv("CLUSTER_REGISTRY_PATH", raising=False)
    monkeypatch.delenv("DEFAULT_CLUSTER_ALIAS", raising=False)
    monkeypatch.delenv("CLUSTER_NAME", raising=False)
    monkeypatch.setattr(
        reporting,
        "get_backend_cr",
        lambda namespace, name, cluster=None: {"kind": "Backend", "metadata": {"name": name, "namespace": namespace}},
    )

    normalized = reporting._resolve_backend_convenience_target(
        reporting._normalized_request(
            InvestigationReportRequest(
                cluster="current-context",
                namespace="operator-smoke",
                target="Backend/crashy",
                include_related_data=False,
            )
        )
    )

    assert normalized.cluster is None
    assert normalized.target == "deployment/crashy"
    assert normalized.service_name == "crashy"
