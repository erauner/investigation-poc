from fastapi.testclient import TestClient

from investigation_service.analysis import derive_findings
from investigation_service.main import app
from investigation_service.models import CollectAlertContextRequest, CollectedContextResponse, Finding, TargetRef
from investigation_service.tools import _infer_alert_inputs


def test_collect_context_accepts_profile_fields(monkeypatch) -> None:
    def fake_collect(_req):
        return CollectedContextResponse(
            target=TargetRef(namespace="default", kind="pod", name="api-123"),
            object_state={"namespace": "default", "kind": "pod", "name": "api-123"},
            events=["no related events"],
            log_excerpt="ok",
            metrics={"prometheus_available": True},
            findings=[
                Finding(
                    severity="info",
                    source="heuristic",
                    title="No Critical Signals Found",
                    evidence="No obvious failure signature detected from current inputs",
                )
            ],
            limitations=["metric unavailable: service_latency_p95_seconds"],
        )

    monkeypatch.setattr("investigation_service.main.collect_workload_context", fake_collect)
    client = TestClient(app)

    response = client.post(
        "/tools/collect_workload_context",
        json={
            "namespace": "default",
            "target": "pod/api-123",
            "profile": "service",
            "service_name": "api",
            "lookback_minutes": 30,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["target"]["name"] == "api-123"
    assert body["limitations"] == ["metric unavailable: service_latency_p95_seconds"]


def test_investigate_includes_limitations(monkeypatch) -> None:
    def fake_collect(_req):
        return CollectedContextResponse(
            target=TargetRef(namespace="default", kind="deployment", name="api"),
            object_state={"namespace": "default", "kind": "deployment", "name": "api"},
            events=["Normal Created"],
            log_excerpt="ok",
            metrics={"prometheus_available": False},
            findings=[
                Finding(
                    severity="warning",
                    source="prometheus",
                    title="High Service Latency",
                    evidence="p95 latency is 1.234s",
                )
            ],
            limitations=["prometheus unavailable or returned no usable results"],
        )

    monkeypatch.setattr("investigation_service.main.collect_workload_context", fake_collect)
    client = TestClient(app)

    response = client.post(
        "/investigate",
        json={
            "namespace": "default",
            "target": "deployment/api",
            "profile": "service",
            "lookback_minutes": 10,
        },
    )

    assert response.status_code == 200
    evidence = response.json()["evidence"]
    assert any("Limitations:" in item for item in evidence)


def test_collect_alert_context_infers_target(monkeypatch) -> None:
    normalized = _infer_alert_inputs(
        CollectAlertContextRequest(
            alertname="PodCrashLooping",
            labels={"namespace": "kagent-smoke", "pod": "api-123"},
        )
    )

    assert normalized.namespace == "kagent-smoke"
    assert normalized.target == "pod/api-123"
    assert normalized.profile == "workload"
    assert normalized.service_name is None


def test_collect_alert_context_infers_service_profile() -> None:
    normalized = _infer_alert_inputs(
        CollectAlertContextRequest(
            alertname="EnvoyHighErrorRate",
            labels={"namespace": "kagent", "service": "kagent-controller"},
        )
    )

    assert normalized.namespace == "kagent"
    assert normalized.target == "service/kagent-controller"
    assert normalized.profile == "service"
    assert normalized.service_name == "kagent-controller"


def test_collect_alert_context_infers_node_target_from_summary() -> None:
    normalized = _infer_alert_inputs(
        CollectAlertContextRequest(
            alertname="NodeHighMemoryAllocation",
            annotations={"summary": "Node worker3 memory allocation at 86.8%"},
        )
    )

    assert normalized.namespace is None
    assert normalized.target == "node/worker3"
    assert normalized.profile == "workload"


def test_collect_alert_context_accepts_explicit_node_target() -> None:
    normalized = _infer_alert_inputs(
        CollectAlertContextRequest(
            alertname="NodeHighMemoryAllocation",
            target="node/worker3",
        )
    )

    assert normalized.namespace is None
    assert normalized.target == "node/worker3"
    assert normalized.profile == "workload"


def test_collect_alert_context_accepts_explicit_node_name() -> None:
    normalized = _infer_alert_inputs(
        CollectAlertContextRequest(
            alertname="NodeHighMemoryAllocation",
            node_name="worker3",
        )
    )

    assert normalized.namespace is None
    assert normalized.target == "node/worker3"
    assert normalized.profile == "workload"


def test_collect_alert_context_route_returns_context(monkeypatch) -> None:
    def fake_collect(_req):
        return CollectedContextResponse(
            target=TargetRef(namespace="kagent-smoke", kind="pod", name="api-123"),
            object_state={"namespace": "kagent-smoke", "kind": "pod", "name": "api-123"},
            events=["BackOff restarting failed container"],
            log_excerpt="starting",
            metrics={"prometheus_available": True},
            findings=[
                Finding(
                    severity="critical",
                    source="events",
                    title="Crash Loop Detected",
                    evidence="Events indicate BackOff/CrashLoopBackOff behavior",
                )
            ],
            limitations=["alertname: PodCrashLooping"],
        )

    monkeypatch.setattr("investigation_service.main.collect_alert_context", fake_collect)
    client = TestClient(app)

    response = client.post(
        "/tools/collect_alert_context",
        json={
            "alertname": "PodCrashLooping",
            "labels": {
                "namespace": "kagent-smoke",
                "pod": "api-123",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["target"] == {
        "namespace": "kagent-smoke",
        "kind": "pod",
        "name": "api-123",
    }
    assert "alertname: PodCrashLooping" in body["limitations"]


def test_node_findings_distinguish_request_saturation_from_pressure() -> None:
    findings = derive_findings(
        "workload",
        {
            "kind": "node",
            "conditions": [{"type": "Ready", "status": "True"}],
        },
        [],
        "",
        {
            "node_memory_allocatable_bytes": 100.0,
            "node_memory_request_bytes": 99.0,
            "node_memory_working_set_bytes": 55.0,
        },
    )

    saturation_finding = next(item for item in findings if item.title == "High Node Memory Request Saturation")
    assert "request saturation more than active node memory pressure" in saturation_finding.evidence
