from fastapi.testclient import TestClient

from investigation_service.main import app
from investigation_service.models import CollectedContextResponse, Finding, TargetRef


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
