from fastapi.testclient import TestClient

from investigation_service.analysis import derive_findings
from investigation_service.main import app
from investigation_service.models import (
    CollectAlertContextRequest,
    CollectedContextResponse,
    Finding,
    TargetRef,
)
from investigation_service.tools import normalize_alert_input


def _sample_response(target: TargetRef) -> CollectedContextResponse:
    return CollectedContextResponse(
        target=target,
        object_state={"namespace": target.namespace, "kind": target.kind, "name": target.name},
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
        enrichment_hints=["service metrics unavailable; use observability MCP for logs, traces, or dashboards"],
    )


def test_collect_context_accepts_profile_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.collect_workload_context",
        lambda _req: _sample_response(TargetRef(namespace="default", kind="pod", name="api-123")),
    )
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
    assert body["enrichment_hints"]


def test_investigate_includes_limitations(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.collect_workload_context",
        lambda _req: CollectedContextResponse(
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
            enrichment_hints=["service metrics unavailable; use observability MCP for logs, traces, or dashboards"],
        ),
    )
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


def test_normalize_alert_input_infers_workload_target() -> None:
    normalized = normalize_alert_input(
        CollectAlertContextRequest(
            alertname="PodCrashLooping",
            labels={"namespace": "kagent-smoke", "pod": "api-123"},
        )
    )

    assert normalized.namespace == "kagent-smoke"
    assert normalized.target == "pod/api-123"
    assert normalized.scope == "workload"
    assert normalized.profile == "workload"
    assert normalized.service_name is None
    assert normalized.normalization_notes


def test_normalize_alert_input_infers_service_profile() -> None:
    normalized = normalize_alert_input(
        CollectAlertContextRequest(
            alertname="EnvoyHighErrorRate",
            labels={"namespace": "kagent", "service": "kagent-controller"},
        )
    )

    assert normalized.namespace == "kagent"
    assert normalized.target == "service/kagent-controller"
    assert normalized.scope == "service"
    assert normalized.profile == "service"
    assert normalized.service_name == "kagent-controller"


def test_normalize_alert_input_accepts_explicit_service_name() -> None:
    normalized = normalize_alert_input(
        CollectAlertContextRequest(
            alertname="EnvoyHighErrorRate",
            namespace="kagent",
            service_name="kagent-controller",
        )
    )

    assert normalized.namespace == "kagent"
    assert normalized.target == "service/kagent-controller"
    assert normalized.scope == "service"
    assert normalized.profile == "service"
    assert normalized.service_name == "kagent-controller"


def test_normalize_alert_input_infers_node_target_from_summary() -> None:
    normalized = normalize_alert_input(
        CollectAlertContextRequest(
            alertname="NodeHighMemoryAllocation",
            annotations={"summary": "Node worker3 memory allocation at 86.8%"},
        )
    )

    assert normalized.namespace is None
    assert normalized.target == "node/worker3"
    assert normalized.scope == "node"
    assert normalized.node_name == "worker3"


def test_normalize_alert_input_accepts_explicit_node_target() -> None:
    normalized = normalize_alert_input(
        CollectAlertContextRequest(
            alertname="NodeHighMemoryAllocation",
            target="node/worker3",
        )
    )

    assert normalized.namespace is None
    assert normalized.target == "node/worker3"
    assert normalized.scope == "node"


def test_normalize_alert_input_accepts_explicit_node_name() -> None:
    normalized = normalize_alert_input(
        CollectAlertContextRequest(
            alertname="NodeHighMemoryAllocation",
            node_name="worker3",
        )
    )

    assert normalized.namespace is None
    assert normalized.target == "node/worker3"
    assert normalized.scope == "node"
    assert normalized.node_name == "worker3"


def test_normalize_alert_route_returns_normalized_request(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.normalize_alert_input",
        lambda _req: normalize_alert_input(
            CollectAlertContextRequest(
                alertname="NodeHighMemoryAllocation",
                node_name="worker3",
            )
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/tools/normalize_alert_input",
        json={"alertname": "NodeHighMemoryAllocation", "node_name": "worker3"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "node"
    assert body["target"] == "node/worker3"


def test_collect_node_context_route_returns_context(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.collect_node_context",
        lambda _req: _sample_response(TargetRef(namespace=None, kind="node", name="worker3")),
    )
    client = TestClient(app)

    response = client.post("/tools/collect_node_context", json={"node_name": "worker3"})

    assert response.status_code == 200
    body = response.json()
    assert body["target"] == {"namespace": None, "kind": "node", "name": "worker3"}


def test_collect_service_context_route_returns_context(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.collect_service_context",
        lambda _req: _sample_response(TargetRef(namespace="kagent", kind="service", name="kagent-controller")),
    )
    client = TestClient(app)

    response = client.post(
        "/tools/collect_service_context",
        json={"namespace": "kagent", "service_name": "kagent-controller"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["target"] == {
        "namespace": "kagent",
        "kind": "service",
        "name": "kagent-controller",
    }


def test_collect_alert_context_route_returns_context(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.collect_alert_context",
        lambda _req: CollectedContextResponse(
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
            enrichment_hints=["normalization completed before collection"],
        ),
    )
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
