import json

from fastapi.testclient import TestClient

from investigation_service.analysis import derive_findings
from investigation_service.k8s_adapter import find_unhealthy_workloads as find_unhealthy_workloads_impl
from investigation_service.k8s_adapter import get_k8s_object
from investigation_service.main import app
from investigation_service.models import (
    BuildRootCauseReportRequest,
    CollectAlertContextRequest,
    CollectServiceContextRequest,
    CollectedContextResponse,
    Finding,
    RootCauseReport,
    TargetRef,
    UnhealthyPodResponse,
    UnhealthyWorkloadsResponse,
)
from investigation_service.reporting import build_root_cause_report as build_root_cause_report_from_request
from investigation_service.synthesis import build_root_cause_report
from investigation_service.tools import collect_service_context, normalize_alert_input


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


def test_collect_service_context_canonicalizes_bare_service_target(monkeypatch) -> None:
    captured = {}

    def fake_collect(_req):
        captured["target"] = _req.target
        return _sample_response(TargetRef(namespace="kagent", kind="service", name="kagent-controller"))

    monkeypatch.setattr("investigation_service.tools._collect_context", fake_collect)

    response = collect_service_context(
        CollectServiceContextRequest(
            namespace="kagent",
            service_name="kagent-controller",
            target="kagent-controller",
        )
    )

    assert captured["target"] == "service/kagent-controller"
    assert response.target.kind == "service"


def test_find_unhealthy_workloads_route_returns_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.find_unhealthy_workloads",
        lambda _req: UnhealthyWorkloadsResponse(
            namespace="kagent-smoke",
            candidates=[
                {
                    "target": "pod/crashy-abc123",
                    "namespace": "kagent-smoke",
                    "kind": "pod",
                    "name": "crashy-abc123",
                    "phase": "Running",
                    "reason": "CrashLoopBackOff",
                    "restart_count": 7,
                    "ready": False,
                    "summary": "CrashLoopBackOff; restarts=7",
                }
            ],
            limitations=[],
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/tools/find_unhealthy_workloads",
        json={"namespace": "kagent-smoke", "limit": 3},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["namespace"] == "kagent-smoke"
    assert body["candidates"][0]["target"] == "pod/crashy-abc123"
    assert body["candidates"][0]["reason"] == "CrashLoopBackOff"


def test_find_unhealthy_pod_route_returns_best_candidate(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.find_unhealthy_pod",
        lambda _req: UnhealthyPodResponse(
            namespace="kagent-smoke",
            candidate={
                "target": "pod/crashy-abc123",
                "namespace": "kagent-smoke",
                "kind": "pod",
                "name": "crashy-abc123",
                "phase": "Running",
                "reason": "CrashLoopBackOff",
                "restart_count": 7,
                "ready": False,
                "summary": "CrashLoopBackOff; restarts=7",
            },
            limitations=[],
        ),
    )
    client = TestClient(app)

    response = client.post("/tools/find_unhealthy_pod", json={"namespace": "kagent-smoke"})

    assert response.status_code == 200
    body = response.json()
    assert body["candidate"]["target"] == "pod/crashy-abc123"
    assert body["candidate"]["reason"] == "CrashLoopBackOff"


def test_find_unhealthy_workloads_prefers_crashlooping_pods(monkeypatch) -> None:
    pods_payload = {
        "items": [
            {
                "metadata": {"name": "whoami-123", "namespace": "kagent-smoke"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"ready": True, "restartCount": 0, "state": {"running": {}}}],
                },
            },
            {
                "metadata": {"name": "crashy-abc123", "namespace": "kagent-smoke"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {
                            "ready": False,
                            "restartCount": 12,
                            "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                        }
                    ],
                },
            },
        ]
    }

    monkeypatch.setattr(
        "investigation_service.k8s_adapter._run_kubectl",
        lambda _args: (True, json.dumps(pods_payload)),
    )

    response = find_unhealthy_workloads_impl(namespace="kagent-smoke", limit=5)

    assert response.limitations == []
    assert len(response.candidates) == 1
    assert response.candidates[0].target == "pod/crashy-abc123"
    assert response.candidates[0].reason == "CrashLoopBackOff"


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


def test_build_root_cause_report_route_returns_typed_report(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.build_root_cause_report_from_request",
        lambda _req: RootCauseReport(
            scope="workload",
            target="pod/crashy-abc123",
            diagnosis="Container Restart Failure Details",
            likely_cause="Container command 'sh -c echo starting && sleep 2 && exit 1' is exiting with code 1, driving repeated CrashLoopBackOff restarts.",
            confidence="high",
            evidence=["k8s: Container Restart Failure Details - exit code=1"],
            limitations=[],
            recommended_next_step="Confirm the failure with describe output, recent logs, and rollout history before taking write actions.",
            suggested_follow_ups=[],
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/tools/build_root_cause_report",
        json={"namespace": "kagent-smoke", "target": "pod/crashy-abc123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["target"] == "pod/crashy-abc123"
    assert body["confidence"] == "high"


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


def test_workload_findings_include_container_restart_details() -> None:
    findings = derive_findings(
        "workload",
        {
            "kind": "pod",
            "containers": [
                {
                    "name": "crashy",
                    "waitingReason": "CrashLoopBackOff",
                    "lastTerminationReason": "Error",
                    "lastTerminationExitCode": 1,
                    "command": ["sh", "-c"],
                    "args": ["echo starting && sleep 2 && exit 1"],
                }
            ],
        },
        [],
        "starting",
        {
            "pod_restart_rate": 0.1,
        },
    )

    detail_finding = next(item for item in findings if item.title == "Container Restart Failure Details")
    assert "exit code=1" in detail_finding.evidence
    assert "command='sh -c echo starting && sleep 2 && exit 1'" in detail_finding.evidence


def test_build_root_cause_report_prefers_direct_restart_evidence() -> None:
    report = build_root_cause_report(
        CollectedContextResponse(
            target=TargetRef(namespace="kagent-smoke", kind="pod", name="crashy-abc123"),
            object_state={"kind": "pod", "name": "crashy-abc123"},
            events=["BackOff restarting failed container crashy in pod crashy-abc123"],
            log_excerpt="starting",
            metrics={"pod_restart_rate": 0.2, "prometheus_available": True},
            findings=[
                Finding(
                    severity="warning",
                    source="prometheus",
                    title="Pod Restarts Increasing",
                    evidence="Restart rate over lookback window: 0.2000/s",
                ),
                Finding(
                    severity="critical",
                    source="k8s",
                    title="Container Restart Failure Details",
                    evidence="waiting reason=CrashLoopBackOff, last termination reason=Error, exit code=1, command='sh -c echo starting && sleep 2 && exit 1'",
                ),
                Finding(
                    severity="critical",
                    source="events",
                    title="Crash Loop Detected",
                    evidence="Events indicate BackOff/CrashLoopBackOff behavior",
                ),
            ],
            limitations=[],
            enrichment_hints=[],
        ),
        BuildRootCauseReportRequest(namespace="kagent-smoke", target="pod/crashy-abc123"),
    )

    assert report.diagnosis == "Container Restart Failure Details"
    assert report.confidence == "high"
    assert report.likely_cause is not None
    assert "exiting with code 1" in report.likely_cause


def test_build_root_cause_report_from_request_collects_node_context(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.reporting.collect_node_context",
        lambda _req: CollectedContextResponse(
            target=TargetRef(namespace=None, kind="node", name="worker3"),
            object_state={"kind": "node", "conditions": [{"type": "Ready", "status": "False"}]},
            events=["NodeNotReady"],
            log_excerpt="",
            metrics={"prometheus_available": True},
            findings=[
                Finding(
                    severity="critical",
                    source="k8s",
                    title="Node Not Ready",
                    evidence="Node condition Ready=False",
                )
            ],
            limitations=[],
            enrichment_hints=[],
        ),
    )

    report = build_root_cause_report_from_request(
        BuildRootCauseReportRequest(target="node/worker3", lookback_minutes=20)
    )

    assert report.scope == "node"
    assert report.target == "node/worker3"
    assert report.diagnosis == "Node Not Ready"


def test_build_root_cause_report_from_request_canonicalizes_service_target(monkeypatch) -> None:
    captured = {}

    def fake_collect_service(req):
        captured["target"] = req.target
        captured["service_name"] = req.service_name
        return CollectedContextResponse(
            target=TargetRef(namespace="observability", kind="service", name="giraffe-kube-prometheus-st-prometheus"),
            object_state={"kind": "service", "name": "giraffe-kube-prometheus-st-prometheus"},
            events=["no related events"],
            log_excerpt="logs only supported for pod or deployment targets",
            metrics={"service_latency_p95_seconds": 1.5, "prometheus_available": True},
            findings=[
                Finding(
                    severity="warning",
                    source="prometheus",
                    title="High Service Latency",
                    evidence="p95 latency is 1.500s",
                )
            ],
            limitations=[],
            enrichment_hints=[],
        )

    monkeypatch.setattr("investigation_service.reporting.collect_service_context", fake_collect_service)

    report = build_root_cause_report_from_request(
        BuildRootCauseReportRequest(
            namespace="observability",
            target="giraffe-kube-prometheus-st-prometheus",
            profile="service",
            service_name="giraffe-kube-prometheus-st-prometheus",
        )
    )

    assert captured["target"] == "service/giraffe-kube-prometheus-st-prometheus"
    assert captured["service_name"] == "giraffe-kube-prometheus-st-prometheus"
    assert report.scope == "service"
    assert report.diagnosis == "High Service Latency"


def test_get_k8s_object_includes_pod_container_details(monkeypatch) -> None:
    payload = {
        "metadata": {"name": "crashy-abc123", "creationTimestamp": "2026-03-06T00:00:00Z"},
        "spec": {
            "containers": [
                {
                    "name": "crashy",
                    "image": "busybox:1.36",
                    "command": ["sh", "-c"],
                    "args": ["echo starting && sleep 2 && exit 1"],
                }
            ]
        },
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "crashy",
                    "ready": False,
                    "restartCount": 12,
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    "lastState": {"terminated": {"reason": "Error", "exitCode": 1}},
                }
            ]
        },
    }

    monkeypatch.setattr(
        "investigation_service.k8s_adapter._run_kubectl",
        lambda _args: (True, json.dumps(payload)),
    )

    result = get_k8s_object(TargetRef(namespace="kagent-smoke", kind="pod", name="crashy-abc123"))

    assert result["containers"][0]["lastTerminationExitCode"] == 1
    assert result["containers"][0]["waitingReason"] == "CrashLoopBackOff"
    assert result["containers"][0]["command"] == ["sh", "-c"]
