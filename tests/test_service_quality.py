from investigation_service.models import CollectContextRequest, InvestigationReportRequest, TargetRef
from investigation_service.prom_adapter import collect_metrics_for_scope
from investigation_service.reporting import _normalized_request
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


def test_manual_service_request_promotes_profile_to_service() -> None:
    normalized = _normalized_request(
        InvestigationReportRequest(
            namespace="observability",
            target="service/api",
            profile="workload",
        )
    )

    assert normalized.scope == "service"
    assert normalized.profile == "service"
    assert "profile promoted to service based on target" in normalized.normalization_notes
