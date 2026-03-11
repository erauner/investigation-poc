import pytest

from investigation_service.analysis import derive_findings
from investigation_service.models import CollectContextRequest, InvestigationReportRequest, TargetRef
from investigation_service import planner, reporting
from investigation_service.prom_adapter import collect_metrics_for_scope
from investigation_service.tools import _collect_context


def test_collect_metrics_for_service_excludes_global_otel_noise(monkeypatch) -> None:
    monkeypatch.setattr("investigation_service.prom_adapter.query_instant", lambda query: None)

    metrics, limitations = collect_metrics_for_scope(
        TargetRef(namespace="observability", kind="service", name="api"),
        profile="service",
        service_name="api",
        lookback_minutes=15,
    )

    assert metrics["prometheus_available"] is False
    assert "metric unavailable: service_request_rate" in limitations
    assert "metric unavailable: accepted_spans_per_sec" not in limitations
    assert "prometheus unavailable or returned no usable results" in limitations


def test_collect_context_for_service_skips_pod_logs(monkeypatch) -> None:
    target = TargetRef(namespace="observability", kind="service", name="api")
    monkeypatch.setattr("investigation_service.tools.resolve_target", lambda namespace, value: target)
    monkeypatch.setattr("investigation_service.tools.resolve_runtime_target", lambda value: value)
    monkeypatch.setattr(
        "investigation_service.tools.get_k8s_object",
        lambda value: {"namespace": "observability", "kind": "service", "name": "api"},
    )
    monkeypatch.setattr("investigation_service.tools.get_related_events", lambda value: ["no related events"])
    monkeypatch.setattr(
        "investigation_service.tools.get_pod_logs",
        lambda value, tail=200: (_ for _ in ()).throw(AssertionError("service path should not request pod logs")),
    )
    monkeypatch.setattr(
        "investigation_service.tools.collect_metrics_for_scope",
        lambda target, profile, service_name, lookback_minutes: (
            {"profile": "service", "prometheus_available": False},
            ["prometheus unavailable or returned no usable results"],
        ),
    )
    monkeypatch.setattr("investigation_service.tools.derive_findings", lambda profile, object_state, events, logs, metrics: [])

    context = _collect_context(
        CollectContextRequest(
            namespace="observability",
            target="service/api",
            profile="service",
            service_name="api",
            lookback_minutes=15,
        )
    )

    assert context.log_excerpt == ""
    assert "pod logs unavailable for target" not in context.limitations
    assert not any("operator-managed workload" in item for item in context.enrichment_hints)


def test_collect_context_adds_operator_ownership_hint_for_workload(monkeypatch) -> None:
    target = TargetRef(namespace="operator-smoke", kind="pod", name="crashy-abc123")
    monkeypatch.setattr("investigation_service.tools.resolve_target", lambda namespace, value: target)
    monkeypatch.setattr("investigation_service.tools.resolve_runtime_target", lambda value: value)
    monkeypatch.setattr(
        "investigation_service.tools.get_k8s_object",
        lambda value: {
            "namespace": "operator-smoke",
            "kind": "pod",
            "name": "crashy-abc123",
            "labels": {
                "app.kubernetes.io/managed-by": "homelab-operator",
                "homelab.erauner.dev/owner-kind": "Backend",
                "homelab.erauner.dev/owner-name": "crashy",
            },
            "ownerReferences": [{"kind": "ReplicaSet", "name": "crashy-65f89648f4"}],
        },
    )
    monkeypatch.setattr("investigation_service.tools.get_related_events", lambda value: ["no related events"])
    monkeypatch.setattr("investigation_service.tools.get_pod_logs", lambda value, tail=200: "starting\nexit 17")
    monkeypatch.setattr(
        "investigation_service.tools.collect_metrics_for_scope",
        lambda target, profile, service_name, lookback_minutes: ({"prometheus_available": True}, []),
    )
    monkeypatch.setattr("investigation_service.tools.derive_findings", lambda profile, object_state, events, logs, metrics: [])

    context = _collect_context(
        CollectContextRequest(
            namespace="operator-smoke",
            target="pod/crashy-abc123",
            profile="workload",
            lookback_minutes=15,
        )
    )

    assert any("operator-managed workload (homelab-operator)" in item for item in context.enrichment_hints)
    assert any("Backend/crashy" in item for item in context.enrichment_hints)


def test_manual_service_request_promotes_profile_to_service() -> None:
    normalized = planner.normalized_request(
        InvestigationReportRequest(
            namespace="observability",
            target="service/api",
            profile="workload",
        ),
        reporting._planner_deps(),
    )

    assert normalized.scope == "service"
    assert normalized.profile == "service"
    assert "profile promoted to service based on target" in normalized.normalization_notes


def test_manual_backend_target_stays_workload_scope_even_with_service_profile() -> None:
    normalized = planner.normalized_request(
        InvestigationReportRequest(
            namespace="operator-smoke",
            target="Backend/crashy",
            profile="service",
        ),
        reporting._planner_deps(),
    )

    assert normalized.scope == "workload"
    assert normalized.profile == "service"


def test_manual_frontend_target_stays_workload_scope_even_with_service_profile() -> None:
    normalized = planner.normalized_request(
        InvestigationReportRequest(
            namespace="operator-smoke",
            target="Frontend/landing",
            profile="service",
        ),
        reporting._planner_deps(),
    )

    assert normalized.scope == "workload"
    assert normalized.profile == "service"


def test_manual_cluster_target_stays_workload_scope_even_with_service_profile() -> None:
    with pytest.raises(ValueError, match="cluster lookup failed for testapp:"):
        planner.normalized_request(
            InvestigationReportRequest(
                namespace="operator-smoke",
                target="Cluster/testapp",
                profile="service",
            ),
            reporting._planner_deps(),
        )


def test_collect_context_for_workload_enriches_with_service_metrics(monkeypatch) -> None:
    target = TargetRef(namespace="metrics-smoke", kind="deployment", name="metrics-api")
    monkeypatch.setattr("investigation_service.tools.resolve_target", lambda namespace, value: target)
    monkeypatch.setattr("investigation_service.tools.resolve_runtime_target", lambda value: value)
    monkeypatch.setattr(
        "investigation_service.tools.get_k8s_object",
        lambda value: {"namespace": "metrics-smoke", "kind": "deployment", "name": "metrics-api"},
    )
    monkeypatch.setattr("investigation_service.tools.get_related_events", lambda value: ["deployment available"])
    monkeypatch.setattr("investigation_service.tools.get_pod_logs", lambda value, tail=200: "healthy")
    monkeypatch.setattr(
        "investigation_service.tools.collect_metrics_for_scope",
        lambda target, profile, service_name, lookback_minutes: (
            {
                "profile": "workload",
                "prometheus_available": True,
                "pod_restart_rate": 0.0,
            },
            [],
        ),
    )
    monkeypatch.setattr(
        "investigation_service.tools.collect_service_enrichment_metrics",
        lambda namespace, service_name, lookback_minutes: (
            {
                "service_request_rate": 4.2,
                "service_error_rate": 0.3,
                "service_latency_p95_seconds": 1.7,
            },
            [],
        ),
    )

    context = _collect_context(
        CollectContextRequest(
            namespace="metrics-smoke",
            target="deployment/metrics-api",
            profile="workload",
            service_name="metrics-api",
            lookback_minutes=15,
        )
    )

    assert context.metrics["service_request_rate"] == 4.2
    assert context.metrics["service_error_rate"] == 0.3
    assert context.metrics["service_latency_p95_seconds"] == 1.7
    titles = {item.title for item in context.findings}
    assert "Service Returning 5xx Responses" in titles
    assert "High Service Latency" in titles


def test_collect_metrics_for_service_uses_best_available_query_family(monkeypatch) -> None:
    def fake_query(query: str, prometheus_url: str | None = None) -> float | None:
        if 'http_server_request_duration_seconds_count{namespace="observability",service="envoy-gateway",status=~"5.."}' in query:
            return 0.12
        if 'http_server_request_duration_seconds_count{namespace="observability",service="envoy-gateway"}' in query:
            return 12.0
        if 'http_server_request_duration_seconds_bucket{namespace="observability",service="envoy-gateway"}' in query:
            return 1.8
        return None

    monkeypatch.setattr("investigation_service.prom_adapter.query_instant", fake_query)

    metrics, limitations = collect_metrics_for_scope(
        TargetRef(namespace="observability", kind="service", name="envoy-gateway"),
        profile="service",
        service_name="envoy-gateway",
        lookback_minutes=15,
    )

    assert metrics["prometheus_available"] is True
    assert metrics["service_metric_family"] == "http_server_service"
    assert metrics["service_request_rate"] == 12.0
    assert metrics["service_error_rate"] == 0.12
    assert metrics["service_latency_p95_seconds"] == 1.8
    assert limitations == []


def test_collect_metrics_for_service_does_not_treat_metric_family_metadata_as_prometheus_success(monkeypatch) -> None:
    monkeypatch.setattr("investigation_service.prom_adapter.query_instant", lambda query, prometheus_url=None: None)

    metrics, limitations = collect_metrics_for_scope(
        TargetRef(namespace="observability", kind="service", name="envoy-gateway"),
        profile="service",
        service_name="envoy-gateway",
        lookback_minutes=15,
    )

    assert metrics["service_metric_family"] == "http_server_service"
    assert metrics["prometheus_available"] is False
    assert "prometheus unavailable or returned no usable results" in limitations


def test_service_findings_use_backend_topology_when_metrics_are_weak() -> None:
    findings = derive_findings(
        "service",
        {
            "kind": "service",
            "name": "envoy-gateway",
            "selector": {"app": "envoy-gateway"},
            "matchedPodCount": 2,
            "readyPodCount": 0,
            "matchedPods": [
                {"name": "envoy-a", "ready": False, "restartCount": 3},
                {"name": "envoy-b", "ready": False, "restartCount": 1},
            ],
            "matchedWorkloads": [{"kind": "deployment", "name": "envoy-gateway"}],
        },
        ["Warning Unhealthy service/envoy-gateway"],
        "",
        {"profile": "service", "prometheus_available": False},
    )

    titles = {item.title for item in findings}
    assert "Service Has No Ready Backends" in titles
    assert "Service Backends Restarting" in titles


def test_service_findings_include_error_like_log_patterns() -> None:
    findings = derive_findings(
        "service",
        {
            "kind": "service",
            "name": "api",
            "selector": {"app": "api"},
            "matchedPodCount": 1,
            "readyPodCount": 1,
            "matchedPods": [{"name": "api-abc123", "ready": True, "restartCount": 0}],
        },
        [],
        "error: upstream returned 500\nexception: timeout",
        {"profile": "service", "prometheus_available": False},
    )

    titles = {item.title for item in findings}
    assert "Error-like Log Patterns" in titles
