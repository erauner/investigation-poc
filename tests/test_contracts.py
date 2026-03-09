import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from investigation_service.analysis import derive_findings
from investigation_service.correlation import collect_correlated_changes
from investigation_service.k8s_adapter import find_unhealthy_workloads as find_unhealthy_workloads_impl
from investigation_service.k8s_adapter import get_k8s_object
from investigation_service.main import app
from investigation_service.models import (
    ActualRoute,
    BuildInvestigationPlanRequest,
    BuildRootCauseReportRequest,
    CollectAlertContextRequest,
    CollectCorrelatedChangesRequest,
    CollectedContextResponse,
    CorrelatedChangesResponse,
    EvidenceBatchExecution,
    EvidenceItem,
    GetActiveEvidenceBatchRequest,
    Finding,
    InvestigationAnalysis,
    InvestigationPlan,
    InvestigationReport,
    InvestigationReportingRequest,
    InvestigationReportRequest,
    InvestigationTarget,
    SubmitEvidenceArtifactsRequest,
    SubmittedEvidenceReconciliationResult,
    StepArtifact,
    StepRouteProvenance,
    TargetRef,
    UnhealthyPodResponse,
    UnhealthyWorkloadsResponse,
)
from investigation_service.reporting import (
    render_investigation_report,
)
from investigation_service.synthesis import build_root_cause_report
from investigation_service.tools import normalize_alert_input
from investigation_service.tools import evidence_bundle_from_context, render_collected_context


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


def test_evidence_bundle_round_trip_preserves_context_shape() -> None:
    context = _sample_response(TargetRef(namespace="default", kind="pod", name="api-123"))

    bundle = evidence_bundle_from_context(context)
    rendered = render_collected_context(bundle)

    assert rendered == context


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def test_collect_workload_context_route_is_removed_from_public_surface() -> None:
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

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/tools/collect_node_context", {"node_name": "worker3"}),
        ("/tools/collect_service_context", {"namespace": "kagent", "service_name": "kagent-controller"}),
        (
            "/tools/collect_alert_context",
            {"alertname": "PodCrashLooping", "labels": {"namespace": "kagent-smoke", "pod": "api-123"}},
        ),
        ("/tools/build_investigation_report", {"namespace": "kagent-smoke", "target": "pod/crashy-abc123"}),
        (
            "/tools/build_alert_investigation_report",
            {"alertname": "PodCrashLooping", "labels": {"namespace": "kagent-smoke", "pod": "crashy-abc123"}},
        ),
    ],
)
def test_removed_slice7_routes_are_not_public(path: str, payload: dict) -> None:
    client = TestClient(app)

    response = client.post(path, json=payload)

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/tools/normalize_incident_input", {"namespace": "kagent-smoke", "target": "Backend/crashy"}),
        ("/tools/collect_workload_evidence", {"namespace": "kagent-smoke", "target": "pod/crashy-abc123"}),
        (
            "/tools/collect_alert_evidence",
            {"alertname": "PodCrashLooping", "labels": {"namespace": "kagent-smoke", "pod": "crashy-abc123"}},
        ),
        ("/tools/collect_node_evidence", {"node_name": "worker3"}),
        ("/tools/collect_service_evidence", {"namespace": "kagent", "service_name": "kagent-controller"}),
        ("/investigate", {"namespace": "default", "target": "deployment/api"}),
    ],
)
def test_removed_peer_replaced_routes_are_not_public(path: str, payload: dict) -> None:
    client = TestClient(app)

    response = client.post(path, json=payload)

    assert response.status_code == 404


def test_build_investigation_plan_route_returns_plan(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.build_investigation_plan_from_request",
        lambda _req: InvestigationPlan(
            mode="targeted_rca",
            objective="Investigate service/api",
            target=InvestigationTarget(
                source="manual",
                scope="service",
                cluster="erauner-home",
                namespace="default",
                requested_target="service/api",
                target="service/api",
                service_name="api",
                profile="service",
                normalization_notes=["normalized"],
            ),
            steps=[],
            evidence_batches=[],
            active_batch_id="batch-1",
            planning_notes=["normalized"],
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/tools/build_investigation_plan",
        json={"namespace": "default", "target": "service/api", "profile": "service"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "targeted_rca"
    assert body["target"]["target"] == "service/api"
    assert body["active_batch_id"] == "batch-1"
    assert body["planning_notes"] == ["normalized"]


def test_execute_investigation_step_route_returns_execution(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.execute_investigation_step_from_request",
        lambda _req: EvidenceBatchExecution(
            batch_id="batch-1",
            executed_step_ids=["collect-target-evidence"],
            artifacts=[
                StepArtifact(
                    step_id="collect-target-evidence",
                    plane="workload",
                    artifact_type="evidence_bundle",
                    summary=["Collected workload evidence"],
                    limitations=[],
                    route_provenance=StepRouteProvenance(
                        requested_capability="workload_evidence_plane",
                        route_satisfaction="unmatched",
                        actual_route=ActualRoute(
                            source_kind="investigation_internal",
                            mcp_server="investigation-mcp-server",
                            tool_name="collect_workload_evidence",
                            tool_path=["planner._execute_step", "deps.collect_workload_evidence"],
                        ),
                    ),
                )
            ],
            execution_notes=["executed batch-1"],
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/tools/execute_investigation_step",
        json={
            "plan": {
                "mode": "targeted_rca",
                "objective": "Investigate service/api",
                "target": {
                    "source": "manual",
                    "scope": "service",
                    "cluster": "erauner-home",
                    "namespace": "default",
                    "requested_target": "service/api",
                    "target": "service/api",
                    "service_name": "api",
                    "profile": "service",
                    "lookback_minutes": 15,
                    "normalization_notes": [],
                },
                "steps": [],
                "evidence_batches": [],
                "active_batch_id": "batch-1",
                "planning_notes": [],
            },
            "incident": {"namespace": "default", "target": "service/api", "profile": "service"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["batch_id"] == "batch-1"
    assert body["executed_step_ids"] == ["collect-target-evidence"]
    assert body["artifacts"][0]["route_provenance"]["requested_capability"] == "workload_evidence_plane"
    assert body["artifacts"][0]["route_provenance"]["route_satisfaction"] == "unmatched"
    assert body["artifacts"][0]["route_provenance"]["actual_route"]["tool_name"] == "collect_workload_evidence"


def test_get_active_evidence_batch_route_returns_execution_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.get_active_evidence_batch_from_request",
        lambda _req: {
            "batch_id": "batch-1",
            "title": "Initial target evidence",
            "intent": "Collect evidence",
            "subject": {
                "source": "manual",
                "kind": "target",
                "summary": "Investigate service/api",
                "requested_target": "service/api",
                "alertname": None,
            },
            "canonical_target": {
                "source": "manual",
                "scope": "service",
                "cluster": "erauner-home",
                "namespace": "default",
                "requested_target": "service/api",
                "target": "service/api",
                "service_name": "api",
                "profile": "service",
                "lookback_minutes": 15,
                "normalization_notes": [],
            },
            "steps": [
                {
                    "step_id": "collect-target-evidence",
                    "title": "Collect service evidence",
                    "plane": "service",
                    "artifact_type": "evidence_bundle",
                    "requested_capability": "service_evidence_plane",
                    "preferred_mcp_server": "prometheus-mcp-server",
                    "preferred_tool_names": ["execute_query"],
                    "fallback_mcp_server": "kubernetes-mcp-server",
                    "fallback_tool_names": ["resources_get"],
                    "execution_mode": "external_preferred",
                    "execution_inputs": {
                        "request_kind": "target_context",
                        "cluster": "erauner-home",
                        "namespace": "default",
                        "target": "service/api",
                        "profile": "service",
                        "service_name": "api",
                        "node_name": None,
                        "lookback_minutes": 15,
                        "labels": {},
                        "annotations": {},
                        "anchor_timestamp": None,
                        "limit": None,
                        "alertname": None,
                    },
                }
            ],
        },
    )
    client = TestClient(app)

    response = client.post(
        "/tools/get_active_evidence_batch",
        json={
            "plan": {
                "mode": "targeted_rca",
                "objective": "Investigate service/api",
                "target": {
                    "source": "manual",
                    "scope": "service",
                    "cluster": "erauner-home",
                    "namespace": "default",
                    "requested_target": "service/api",
                    "target": "service/api",
                    "service_name": "api",
                    "profile": "service",
                    "lookback_minutes": 15,
                    "normalization_notes": [],
                },
                "steps": [],
                "evidence_batches": [],
                "active_batch_id": "batch-1",
                "planning_notes": [],
            },
            "incident": {"namespace": "default", "target": "service/api", "profile": "service"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["batch_id"] == "batch-1"
    assert body["subject"]["kind"] == "target"
    assert body["steps"][0]["execution_mode"] == "external_preferred"
    assert body["steps"][0]["execution_inputs"]["request_kind"] == "target_context"


def test_submit_evidence_step_artifacts_route_returns_execution_and_updated_plan(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.submit_evidence_step_artifacts_from_request",
        lambda _req: SubmittedEvidenceReconciliationResult(
            execution=EvidenceBatchExecution(
                batch_id="batch-1",
                executed_step_ids=["collect-target-evidence"],
                artifacts=[],
                execution_notes=["reconciled externally submitted evidence for batch-1"],
            ),
            updated_plan=InvestigationPlan(
                mode="targeted_rca",
                objective="Investigate service/api",
                target=InvestigationTarget(
                    source="manual",
                    scope="service",
                    cluster="erauner-home",
                    namespace="default",
                    requested_target="service/api",
                    target="service/api",
                    service_name="api",
                    profile="service",
                    lookback_minutes=15,
                    normalization_notes=[],
                ),
                steps=[
                    {
                        "id": "collect-target-evidence",
                        "title": "Collect service evidence",
                        "category": "evidence",
                        "plane": "service",
                        "status": "completed",
                        "rationale": "Collect target evidence",
                        "suggested_capability": "service_evidence_plane",
                        "preferred_mcp_server": "prometheus-mcp-server",
                        "preferred_tool_names": ["execute_query"],
                        "fallback_mcp_server": "kubernetes-mcp-server",
                        "fallback_tool_names": ["resources_get"],
                        "depends_on": [],
                    }
                ],
                evidence_batches=[
                    {
                        "id": "batch-1",
                        "title": "Initial target evidence",
                        "status": "pending",
                        "intent": "Collect evidence",
                        "step_ids": ["collect-target-evidence", "collect-change-candidates"],
                    }
                ],
                active_batch_id="batch-1",
                planning_notes=["updated plan after executing batch-1"],
            ),
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/tools/submit_evidence_step_artifacts",
        json={
            "plan": {
                "mode": "targeted_rca",
                "objective": "Investigate service/api",
                "target": {
                    "source": "manual",
                    "scope": "service",
                    "cluster": "erauner-home",
                    "namespace": "default",
                    "requested_target": "service/api",
                    "target": "service/api",
                    "service_name": "api",
                    "profile": "service",
                    "lookback_minutes": 15,
                    "normalization_notes": [],
                },
                "steps": [],
                "evidence_batches": [],
                "active_batch_id": "batch-1",
                "planning_notes": [],
            },
            "incident": {"namespace": "default", "target": "service/api", "profile": "service"},
            "submitted_steps": [
                {
                    "step_id": "collect-target-evidence",
                    "evidence_bundle": {
                        "cluster": "erauner-home",
                        "target": {"namespace": "default", "kind": "service", "name": "api"},
                        "object_state": {},
                        "events": [],
                        "log_excerpt": "",
                        "metrics": {},
                        "findings": [],
                        "limitations": [],
                        "enrichment_hints": [],
                    },
                    "actual_route": {
                        "source_kind": "peer_mcp",
                        "mcp_server": "prometheus-mcp-server",
                        "tool_name": "execute_query",
                        "tool_path": ["prometheus-mcp-server", "execute_query"],
                    },
                    "summary": [],
                    "limitations": [],
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["execution"]["batch_id"] == "batch-1"
    assert body["execution"]["executed_step_ids"] == ["collect-target-evidence"]
    assert body["updated_plan"]["active_batch_id"] == "batch-1"


def test_update_investigation_plan_route_returns_plan(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.update_investigation_plan_from_request",
        lambda _req: InvestigationPlan(
            mode="targeted_rca",
            objective="Investigate service/api",
            target=InvestigationTarget(
                source="manual",
                scope="service",
                cluster="erauner-home",
                namespace="default",
                requested_target="service/api",
                target="service/api",
                service_name="api",
                profile="service",
                lookback_minutes=15,
                normalization_notes=["normalized"],
            ),
            steps=[],
            evidence_batches=[],
            active_batch_id=None,
            planning_notes=["updated"],
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/tools/update_investigation_plan",
        json={
            "plan": {
                "mode": "targeted_rca",
                "objective": "Investigate service/api",
                "target": {
                    "source": "manual",
                    "scope": "service",
                    "cluster": "erauner-home",
                    "namespace": "default",
                    "requested_target": "service/api",
                    "target": "service/api",
                    "service_name": "api",
                    "profile": "service",
                    "lookback_minutes": 15,
                    "normalization_notes": [],
                },
                "steps": [],
                "evidence_batches": [],
                "active_batch_id": "batch-1",
                "planning_notes": [],
            },
            "execution": {
                "batch_id": "batch-1",
                "executed_step_ids": ["collect-target-evidence"],
                "artifacts": [],
                "execution_notes": [],
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["active_batch_id"] is None
    assert body["planning_notes"] == ["updated"]


def test_investigate_route_is_removed_from_public_surface() -> None:
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

    assert response.status_code == 404


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


def test_normalize_alert_input_keeps_pod_alerts_in_workload_scope_when_service_label_is_present() -> None:
    normalized = normalize_alert_input(
        CollectAlertContextRequest(
            alertname="PodCrashLooping",
            labels={
                "namespace": "toolbridge",
                "pod": "toolbridge-api-migrate-j5wwf",
                "service": "toolbridge-api",
            },
        )
    )

    assert normalized.target == "pod/toolbridge-api-migrate-j5wwf"
    assert normalized.scope == "workload"
    assert normalized.profile == "workload"
    assert normalized.service_name is None


def test_normalize_alert_input_does_not_treat_app_label_as_concrete_target() -> None:
    with pytest.raises(ValueError, match="target could not be inferred from alert input"):
        normalize_alert_input(
            CollectAlertContextRequest(
                alertname="PlexPodNotReady",
                labels={"namespace": "media", "app": "plex"},
            )
        )


def test_normalize_alert_input_does_not_treat_job_label_as_service_target() -> None:
    with pytest.raises(ValueError, match="target could not be inferred from alert input"):
        normalize_alert_input(
            CollectAlertContextRequest(
                alertname="JobAlert",
                labels={"namespace": "batch", "job": "backup-runner"},
            )
        )


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


def test_resolve_primary_target_route_returns_target_artifact(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.resolve_primary_target_from_request",
        lambda _req: InvestigationTarget(
            source="alert",
            scope="workload",
            cluster="current-context",
            namespace="kagent-smoke",
            requested_target="pod",
            target="pod/crashy-abc123",
            normalization_notes=["resolved vague workload target to pod/crashy-abc123"],
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/tools/resolve_primary_target",
        json={"namespace": "kagent-smoke", "target": "pod"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["requested_target"] == "pod"
    assert body["target"] == "pod/crashy-abc123"


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


def test_find_unhealthy_workloads_includes_init_blocked_pods(monkeypatch) -> None:
    pods_payload = {
        "items": [
            {
                "metadata": {"name": "toolbridge-api-migrate-j5wwf", "namespace": "toolbridge"},
                "status": {
                    "phase": "Pending",
                    "initContainerStatuses": [
                        {
                            "name": "wait-for-postgres",
                            "ready": False,
                            "restartCount": 1,
                            "state": {"waiting": {"reason": "PodInitializing"}},
                            "lastState": {"terminated": {"reason": "Error", "exitCode": 1}},
                        }
                    ],
                    "containerStatuses": [],
                },
            }
        ]
    }

    monkeypatch.setattr(
        "investigation_service.k8s_adapter._run_kubectl",
        lambda _args: (True, json.dumps(pods_payload)),
    )

    response = find_unhealthy_workloads_impl(namespace="toolbridge", limit=5)

    assert response.limitations == []
    assert len(response.candidates) == 1
    assert response.candidates[0].target == "pod/toolbridge-api-migrate-j5wwf"
    assert response.candidates[0].summary.startswith("init blocked:")


def test_build_root_cause_report_route_is_removed_from_public_surface() -> None:
    client = TestClient(app)

    response = client.post(
        "/tools/build_root_cause_report",
        json={"namespace": "kagent-smoke", "target": "pod/crashy-abc123"},
    )

    assert response.status_code == 404


def test_collect_correlated_changes_route_is_removed_from_public_surface() -> None:
    client = TestClient(app)

    response = client.post(
        "/tools/collect_correlated_changes",
        json={"namespace": "kagent-smoke", "target": "pod/crashy-abc123"},
    )

    assert response.status_code == 404


def test_collect_change_candidates_route_returns_ranked_changes(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.collect_change_candidates",
        lambda _req: CorrelatedChangesResponse(
            scope="workload",
            target="pod/crashy-abc123",
            changes=[
                {
                    "fingerprint": "event|pod|kagent-smoke|crashy-abc123|backoff|back-off restarting failed container",
                    "timestamp": _now_iso(),
                    "source": "k8s_event",
                    "resource_kind": "pod",
                    "namespace": "kagent-smoke",
                    "name": "crashy-abc123",
                    "relation": "direct",
                    "summary": "BackOff: restarting failed container",
                    "confidence": "high",
                }
            ],
            limitations=[],
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/tools/collect_change_candidates",
        json={"namespace": "kagent-smoke", "target": "pod/crashy-abc123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["target"] == "pod/crashy-abc123"
    assert body["changes"][0]["confidence"] == "high"


def test_rank_hypotheses_route_returns_analysis(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.rank_hypotheses_from_request",
        lambda _req: InvestigationAnalysis(
            scope="workload",
            target="pod/crashy-abc123",
            profile="workload",
            hypotheses=[
                {
                    "key": "crash-loop",
                    "diagnosis": "Crash Loop Detected",
                    "likely_cause": "The pod is repeatedly failing shortly after start, so Kubernetes is backing off restarts.",
                    "confidence": "high",
                    "score": 42,
                    "supporting_findings": [],
                    "evidence_items": [],
                }
            ],
            limitations=[],
            recommended_next_step="Confirm the failure with describe output and recent logs.",
            suggested_follow_ups=[],
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/tools/rank_hypotheses",
        json={"namespace": "kagent-smoke", "target": "pod/crashy-abc123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["hypotheses"][0]["key"] == "crash-loop"
    assert body["hypotheses"][0]["score"] == 42


def test_render_investigation_report_route_returns_typed_report(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.main.render_investigation_report",
        lambda _req: InvestigationReport(
            scope="workload",
            target="pod/crashy-abc123",
            diagnosis="Crash Loop Detected",
            likely_cause="The pod is repeatedly failing shortly after start, so Kubernetes is backing off restarts.",
            confidence="high",
            evidence=["events: Crash Loop Detected - Events indicate BackOff/CrashLoopBackOff behavior"],
            evidence_items=[],
            related_data=[],
            related_data_note="no meaningful correlated changes found in the requested time window",
            limitations=[],
            recommended_next_step="Confirm the failure with describe output and recent logs.",
            suggested_follow_ups=[],
            normalization_notes=["alertname=PodCrashLooping"],
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/tools/render_investigation_report",
        json={"namespace": "kagent-smoke", "target": "pod/crashy-abc123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["target"] == "pod/crashy-abc123"
    assert body["diagnosis"] == "Crash Loop Detected"


def test_render_investigation_report_route_accepts_execution_context(monkeypatch) -> None:
    captured = {}

    def fake_render(req: InvestigationReportingRequest) -> InvestigationReport:
        captured["execution_context"] = req.execution_context
        return InvestigationReport(
            cluster="test-cluster",
            scope="service",
            target="service/api",
            diagnosis="High Service Latency",
            confidence="medium",
            evidence=["Latency evidence"],
            evidence_items=[],
            related_data=[],
            limitations=[],
            recommended_next_step="Check the upstream dependency.",
        )

    monkeypatch.setattr("investigation_service.main.render_investigation_report", fake_render)
    client = TestClient(app)

    response = client.post(
        "/tools/render_investigation_report",
        json={
            "target": "service/api",
            "profile": "service",
            "execution_context": {
                "updated_plan": {
                    "mode": "targeted_rca",
                    "objective": "Investigate service/api",
                    "target": {
                        "source": "manual",
                        "scope": "service",
                        "cluster": "test-cluster",
                        "namespace": "default",
                        "requested_target": "service/api",
                        "target": "service/api",
                        "service_name": "api",
                        "profile": "service",
                        "lookback_minutes": 15,
                        "normalization_notes": [],
                    },
                    "steps": [],
                    "evidence_batches": [],
                    "planning_notes": [],
                },
                "executions": [],
                "allow_bounded_fallback_execution": False,
            },
        },
    )

    assert response.status_code == 200
    assert captured["execution_context"] is not None
    assert captured["execution_context"].allow_bounded_fallback_execution is False


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
    assert report.evidence_items


def test_render_investigation_report_from_request_collects_node_evidence(monkeypatch) -> None:
    monkeypatch.setattr("investigation_service.reporting.load_guideline_rules", lambda: ([], []))
    monkeypatch.setattr(
        "investigation_service.reporting.execute_investigation_step",
        lambda _req: EvidenceBatchExecution(
            batch_id="batch-1",
            executed_step_ids=["collect-target-evidence"],
            artifacts=[
                {
                    "step_id": "collect-target-evidence",
                    "plane": "node",
                    "artifact_type": "evidence_bundle",
                    "summary": ["Node Not Ready"],
                    "limitations": [],
                    "evidence_bundle": {
                        "cluster": "current-context",
                        "target": {"namespace": None, "kind": "node", "name": "worker3"},
                        "object_state": {"kind": "node", "conditions": [{"type": "Ready", "status": "False"}]},
                        "events": ["NodeNotReady"],
                        "log_excerpt": "",
                        "metrics": {"prometheus_available": True},
                        "findings": [
                            {
                                "severity": "critical",
                                "source": "k8s",
                                "title": "Node Not Ready",
                                "evidence": "Node condition Ready=False",
                            }
                        ],
                        "limitations": [],
                        "enrichment_hints": [],
                    },
                }
            ],
            execution_notes=[],
        ),
    )
    monkeypatch.setattr(
        "investigation_service.reporting.update_investigation_plan",
        lambda req: req.plan.model_copy(update={"active_batch_id": None}),
    )

    report = render_investigation_report(
        InvestigationReportRequest(target="node/worker3", lookback_minutes=20, include_related_data=False)
    )

    assert report.scope == "node"
    assert report.target == "node/worker3"
    assert report.diagnosis == "Node Not Ready"


def test_render_investigation_report_from_request_canonicalizes_service_target(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr("investigation_service.reporting.load_guideline_rules", lambda: ([], []))

    def fake_execute(req):
        captured["target"] = req.plan.target.target
        captured["service_name"] = req.plan.target.service_name
        return EvidenceBatchExecution(
            batch_id="batch-1",
            executed_step_ids=["collect-target-evidence"],
            artifacts=[
                {
                    "step_id": "collect-target-evidence",
                    "plane": "service",
                    "artifact_type": "evidence_bundle",
                    "summary": ["High Service Latency"],
                    "limitations": [],
                    "evidence_bundle": {
                        "cluster": "current-context",
                        "target": {
                            "namespace": "observability",
                            "kind": "service",
                            "name": "giraffe-kube-prometheus-st-prometheus",
                        },
                        "object_state": {"kind": "service", "name": "giraffe-kube-prometheus-st-prometheus"},
                        "events": ["no related events"],
                        "log_excerpt": "logs only supported for pod or deployment targets",
                        "metrics": {"service_latency_p95_seconds": 1.5, "prometheus_available": True},
                        "findings": [
                            {
                                "severity": "warning",
                                "source": "prometheus",
                                "title": "High Service Latency",
                                "evidence": "p95 latency is 1.500s",
                            }
                        ],
                        "limitations": [],
                        "enrichment_hints": [],
                    },
                }
            ],
            execution_notes=[],
        )

    monkeypatch.setattr("investigation_service.reporting.execute_investigation_step", fake_execute)
    monkeypatch.setattr(
        "investigation_service.reporting.update_investigation_plan",
        lambda req: req.plan.model_copy(update={"active_batch_id": None}),
    )

    report = render_investigation_report(
        InvestigationReportRequest(
            namespace="observability",
            target="giraffe-kube-prometheus-st-prometheus",
            profile="service",
            service_name="giraffe-kube-prometheus-st-prometheus",
            include_related_data=False,
        )
    )

    assert captured["target"] == "service/giraffe-kube-prometheus-st-prometheus"
    assert captured["service_name"] == "giraffe-kube-prometheus-st-prometheus"
    assert report.scope == "service"
    assert report.diagnosis == "High Service Latency"


def test_collect_correlated_changes_ranks_direct_workload_events(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.correlation.get_events",
        lambda **_kwargs: [
                {
                    "reason": "BackOff",
                    "message": "Back-off restarting failed container",
                    "lastTimestamp": _now_iso(),
                "involvedObject": {"kind": "Pod", "name": "crashy-abc123", "namespace": "kagent-smoke"},
                "metadata": {"namespace": "kagent-smoke"},
            }
        ],
    )

    response = collect_correlated_changes(
        CollectCorrelatedChangesRequest(namespace="kagent-smoke", target="pod/crashy-abc123")
    )

    assert response.scope == "workload"
    assert response.target == "pod/crashy-abc123"
    assert response.changes[0].relation == "direct"
    assert response.changes[0].confidence == "high"
    assert response.changes[0].fingerprint.startswith("event|pod|")


def test_collect_correlated_changes_infers_service_rollouts(monkeypatch) -> None:
    monkeypatch.setattr("investigation_service.correlation.get_events", lambda **_kwargs: [])
    monkeypatch.setattr(
        "investigation_service.correlation.get_service_related_deployments",
        lambda _namespace, _service_name: [
            {
                "kind": "deployment",
                "namespace": "observability",
                "name": "prometheus",
                "timestamp": _now_iso(),
                "images": ["prometheus:v1"],
            }
        ],
    )

    response = collect_correlated_changes(
        CollectCorrelatedChangesRequest(
            namespace="observability",
            target="service/giraffe-kube-prometheus-st-prometheus",
            profile="service",
            service_name="giraffe-kube-prometheus-st-prometheus",
        )
    )

    assert response.scope == "service"
    assert response.changes[0].source == "rollout"
    assert response.changes[0].relation == "same_service"


def test_collect_correlated_changes_for_target_matches_request_wrapper(monkeypatch) -> None:
    from investigation_service.correlation import collect_correlated_changes_for_target
    from investigation_service.models import InvestigationTarget

    monkeypatch.setattr(
        "investigation_service.correlation.resolve_cluster",
        lambda cluster: type("ResolvedCluster", (), {"alias": cluster or "erauner-home"})(),
    )
    monkeypatch.setattr(
        "investigation_service.correlation.resolve_target",
        lambda namespace, target, cluster=None: TargetRef(namespace=namespace, kind="service", name="api"),
    )
    monkeypatch.setattr(
        "investigation_service.correlation.resolve_runtime_target",
        lambda target, cluster=None: target,
    )
    monkeypatch.setattr(
        "investigation_service.correlation.get_events",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "investigation_service.correlation.get_service_related_deployments",
        lambda _namespace, _service_name, cluster=None: [],
    )

    via_request = collect_correlated_changes(
        CollectCorrelatedChangesRequest(
            cluster="erauner-home",
            namespace="default",
            target="service/api",
            profile="service",
            service_name="api",
            lookback_minutes=60,
            limit=10,
        )
    )
    via_target = collect_correlated_changes_for_target(
        InvestigationTarget(
            source="manual",
            scope="service",
            cluster="erauner-home",
            namespace="default",
            requested_target="service/api",
            target="service/api",
            node_name=None,
            service_name="api",
            profile="service",
            lookback_minutes=60,
            normalization_notes=[],
        ),
        lookback_minutes=60,
        limit=10,
    )

    assert via_target == via_request


def test_render_investigation_report_dedupes_related_changes(monkeypatch) -> None:
    monkeypatch.setattr("investigation_service.reporting.load_guideline_rules", lambda: ([], []))
    monkeypatch.setattr(
        "investigation_service.reporting.execute_investigation_step",
        lambda _req: EvidenceBatchExecution(
            batch_id="batch-1",
            executed_step_ids=["collect-target-evidence", "collect-change-candidates"],
            artifacts=[
                {
                    "step_id": "collect-target-evidence",
                    "plane": "workload",
                    "artifact_type": "evidence_bundle",
                    "summary": ["Crash Loop Detected"],
                    "limitations": [],
                    "evidence_bundle": {
                        "cluster": "current-context",
                        "target": {"namespace": "kagent-smoke", "kind": "pod", "name": "crashy-abc123"},
                        "object_state": {"kind": "pod", "name": "crashy-abc123"},
                        "events": ["BackOff restarting failed container"],
                        "log_excerpt": "starting",
                        "metrics": {"prometheus_available": True},
                        "findings": [
                            {
                                "severity": "critical",
                                "source": "events",
                                "title": "Crash Loop Detected",
                                "evidence": "Events indicate BackOff/CrashLoopBackOff behavior",
                            }
                        ],
                        "limitations": [],
                        "enrichment_hints": [],
                    },
                },
                {
                    "step_id": "collect-change-candidates",
                    "plane": "changes",
                    "artifact_type": "change_candidates",
                    "summary": ["BackOff: restarting failed container"],
                    "limitations": [],
                    "change_candidates": {
                        "cluster": "current-context",
                        "scope": "workload",
                        "target": "pod/crashy-abc123",
                        "changes": [
                            {
                                "fingerprint": "event|pod/crashy-abc123|backoff|restarting failed container",
                                "timestamp": _now_iso(),
                                "source": "k8s_event",
                                "resource_kind": "pod",
                                "namespace": "kagent-smoke",
                                "name": "crashy-abc123",
                                "relation": "direct",
                                "summary": "BackOff: restarting failed container",
                                "confidence": "high",
                            }
                        ],
                        "limitations": [],
                    },
                },
            ],
            execution_notes=[],
        ),
    )
    monkeypatch.setattr(
        "investigation_service.reporting.update_investigation_plan",
        lambda req: req.plan.model_copy(update={"active_batch_id": None}),
    )

    report = render_investigation_report(
        InvestigationReportRequest(namespace="kagent-smoke", target="pod/crashy-abc123")
    )

    assert report.related_data == []
    assert report.related_data_note == "all correlated changes duplicated primary evidence"


def test_render_investigation_report_keeps_empty_related_note_out_of_limitations(monkeypatch) -> None:
    monkeypatch.setattr("investigation_service.reporting.load_guideline_rules", lambda: ([], []))
    monkeypatch.setattr(
        "investigation_service.reporting.execute_investigation_step",
        lambda _req: EvidenceBatchExecution(
            batch_id="batch-1",
            executed_step_ids=["collect-target-evidence", "collect-change-candidates"],
            artifacts=[
                {
                    "step_id": "collect-target-evidence",
                    "plane": "workload",
                    "artifact_type": "evidence_bundle",
                    "summary": ["Crash Loop Detected"],
                    "limitations": ["metric unavailable: accepted_spans_per_second"],
                    "evidence_bundle": {
                        "cluster": "current-context",
                        "target": {"namespace": "kagent-smoke", "kind": "pod", "name": "crashy-abc123"},
                        "object_state": {"kind": "pod", "name": "crashy-abc123"},
                        "events": ["BackOff restarting failed container"],
                        "log_excerpt": "starting",
                        "metrics": {"prometheus_available": True},
                        "findings": [
                            {
                                "severity": "critical",
                                "source": "events",
                                "title": "Crash Loop Detected",
                                "evidence": "Events indicate BackOff/CrashLoopBackOff behavior",
                            }
                        ],
                        "limitations": ["metric unavailable: accepted_spans_per_second"],
                        "enrichment_hints": [],
                    },
                },
                {
                    "step_id": "collect-change-candidates",
                    "plane": "changes",
                    "artifact_type": "change_candidates",
                    "summary": ["No meaningful change candidates found in the requested window"],
                    "limitations": ["no correlated changes found in the requested time window"],
                    "change_candidates": {
                        "cluster": "current-context",
                        "scope": "workload",
                        "target": "pod/crashy-abc123",
                        "changes": [],
                        "limitations": ["no correlated changes found in the requested time window"],
                    },
                },
            ],
            execution_notes=[],
        ),
    )
    monkeypatch.setattr(
        "investigation_service.reporting.update_investigation_plan",
        lambda req: req.plan.model_copy(update={"active_batch_id": None}),
    )

    report = render_investigation_report(
        InvestigationReportRequest(namespace="kagent-smoke", target="pod/crashy-abc123")
    )

    assert report.related_data == []
    assert report.related_data_note == "no meaningful correlated changes found in the requested time window"
    assert "metric unavailable: accepted_spans_per_second" in report.limitations
    assert "no correlated changes found in the requested time window" not in report.limitations
    assert "no meaningful correlated changes found in the requested time window" not in report.limitations


def test_collect_correlated_changes_for_node_includes_recent_scheduling(monkeypatch) -> None:
    monkeypatch.setattr("investigation_service.correlation.get_events", lambda **_kwargs: [])
    monkeypatch.setattr(
        "investigation_service.correlation.get_pods_for_node",
        lambda _node_name, limit=10: [
            {
                "namespace": "default",
                "name": "api-123",
                "creationTimestamp": _now_iso(),
            }
        ],
    )

    response = collect_correlated_changes(
        CollectCorrelatedChangesRequest(target="node/worker3", lookback_minutes=120)
    )

    assert response.scope == "node"
    assert response.changes[0].relation == "same_node"


def test_collect_correlated_changes_ignores_non_change_like_workload_events(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.correlation.get_events",
        lambda **_kwargs: [
            {
                "reason": "Started",
                "message": "Started container crashy",
                "lastTimestamp": _now_iso(),
                "involvedObject": {"kind": "Pod", "name": "crashy-abc123", "namespace": "kagent-smoke"},
                "metadata": {"namespace": "kagent-smoke"},
            },
            {
                "reason": "SandboxChanged",
                "message": "Pod sandbox changed",
                "lastTimestamp": _now_iso(),
                "involvedObject": {"kind": "Pod", "name": "crashy-abc123", "namespace": "kagent-smoke"},
                "metadata": {"namespace": "kagent-smoke"},
            },
        ],
    )

    response = collect_correlated_changes(
        CollectCorrelatedChangesRequest(namespace="kagent-smoke", target="pod/crashy-abc123")
    )

    assert len(response.changes) == 1
    assert response.changes[0].summary.startswith("Started:")


def test_collect_correlated_changes_reports_empty_when_no_meaningful_workload_changes(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.correlation.get_events",
        lambda **_kwargs: [
            {
                "reason": "SandboxChanged",
                "message": "Pod sandbox changed",
                "lastTimestamp": _now_iso(),
                "involvedObject": {"kind": "Pod", "name": "crashy-abc123", "namespace": "kagent-smoke"},
                "metadata": {"namespace": "kagent-smoke"},
            }
        ],
    )

    response = collect_correlated_changes(
        CollectCorrelatedChangesRequest(namespace="kagent-smoke", target="pod/crashy-abc123")
    )

    assert response.changes == []
    assert "no correlated changes found in the requested time window" in response.limitations


def test_get_k8s_object_includes_pod_container_details(monkeypatch) -> None:
    payload = {
        "metadata": {
            "name": "crashy-abc123",
            "creationTimestamp": "2026-03-06T00:00:00Z",
            "labels": {
                "app.kubernetes.io/managed-by": "homelab-operator",
                "homelab.erauner.dev/owner-kind": "Backend",
                "homelab.erauner.dev/owner-name": "crashy",
            },
            "ownerReferences": [
                {"apiVersion": "homelab.erauner.dev/v1alpha1", "kind": "Backend", "name": "crashy"}
            ],
        },
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
    assert result["labels"]["homelab.erauner.dev/owner-kind"] == "Backend"
    assert result["ownerReferences"][0]["kind"] == "Backend"
