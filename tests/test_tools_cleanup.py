import investigation_service.reporting as reporting
import investigation_service.tools as tools
import pytest
from investigation_service.models import (
    CollectAlertContextRequest,
    CollectContextRequest,
    EvidenceBundle,
    InvestigationReportRequest,
    NormalizedInvestigationRequest,
    TargetRef,
)


def test_normalize_alert_input_delegates_to_reporting_normalization(monkeypatch) -> None:
    captured: dict[str, object] = {}
    expected = NormalizedInvestigationRequest(
        source="alert",
        scope="workload",
        cluster="erauner-home",
        namespace="kagent-smoke",
        target="pod/crashy-abc123",
        profile="workload",
        lookback_minutes=15,
        normalization_notes=["delegated"],
    )

    def fake_normalize(req: InvestigationReportRequest) -> NormalizedInvestigationRequest:
        captured["request"] = req
        return expected

    monkeypatch.setattr(reporting, "normalize_investigation_request", fake_normalize)

    request = CollectAlertContextRequest(
        alertname="PodCrashLooping",
        cluster="erauner-home",
        namespace="kagent-smoke",
        target="pod/crashy",
        profile="workload",
        service_name="crashy",
        lookback_minutes=15,
        labels={"pod": "crashy-abc123"},
        annotations={"summary": "pod crashy is restarting"},
        node_name="worker-1",
    )

    normalized = tools.normalize_alert_input(request)

    assert normalized == expected
    assert captured["request"] == InvestigationReportRequest(
        alertname="PodCrashLooping",
        cluster="erauner-home",
        namespace="kagent-smoke",
        target="pod/crashy",
        profile="workload",
        service_name="crashy",
        lookback_minutes=15,
        labels={"pod": "crashy-abc123"},
        annotations={"summary": "pod crashy is restarting"},
        node_name="worker-1",
        question=None,
    )


def test_collect_alert_evidence_uses_canonical_normalization_before_collection(monkeypatch) -> None:
    normalized = NormalizedInvestigationRequest(
        source="alert",
        scope="service",
        cluster="erauner-home",
        namespace="kagent-smoke",
        target="service/crashy",
        profile="service",
        service_name="crashy",
        lookback_minutes=30,
        normalization_notes=["delegated"],
    )
    captured: dict[str, object] = {}
    expected_bundle = EvidenceBundle(
        cluster="erauner-home",
        target=TargetRef(namespace="kagent-smoke", kind="service", name="crashy"),
        object_state={},
        events=[],
        log_excerpt="",
        metrics={},
        findings=[],
        limitations=[],
        enrichment_hints=[],
    )

    monkeypatch.setattr(tools, "normalize_alert_input", lambda _req: normalized)

    def fake_collect(req: CollectContextRequest) -> EvidenceBundle:
        captured["request"] = req
        return expected_bundle

    monkeypatch.setattr(tools, "collect_evidence_bundle", fake_collect)

    bundle = tools.collect_alert_evidence(
        CollectAlertContextRequest(alertname="ServiceAlert", namespace="kagent-smoke", service_name="crashy")
    )

    assert bundle == expected_bundle
    assert captured["request"] == CollectContextRequest(
        cluster="erauner-home",
        namespace="kagent-smoke",
        target="service/crashy",
        profile="service",
        service_name="crashy",
        lookback_minutes=30,
    )


def test_tools_cleanup_removes_duplicate_private_alert_normalization_helpers() -> None:
    assert not hasattr(tools, "_infer_alert_inputs")
    assert not hasattr(tools, "_first_non_empty")
    assert not hasattr(tools, "_label_value")
    assert not hasattr(tools, "_annotation_value")
    assert not hasattr(tools, "_infer_target_from_text")


def test_reporting_resolve_cluster_forwards_labels_when_delegate_supports_them(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_resolve_cluster(cluster: str | None, labels: dict[str, str] | None = None) -> str:
        captured["cluster"] = cluster
        captured["labels"] = labels
        return "erauner-home"

    monkeypatch.setattr(tools, "resolve_cluster", fake_resolve_cluster)

    resolved = reporting.resolve_cluster(None, {"cluster": "erauner-home"})

    assert resolved == "erauner-home"
    assert captured == {
        "cluster": None,
        "labels": {"cluster": "erauner-home"},
    }


def test_reporting_resolve_cluster_reraises_delegate_typeerror(monkeypatch) -> None:
    def fake_resolve_cluster(cluster: str | None, labels: dict[str, str] | None = None) -> str:
        raise TypeError("broken cluster resolver")

    monkeypatch.setattr(tools, "resolve_cluster", fake_resolve_cluster)

    with pytest.raises(TypeError, match="broken cluster resolver"):
        reporting.resolve_cluster(None, {"cluster": "erauner-home"})
