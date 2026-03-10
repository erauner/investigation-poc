from investigation_orchestrator import evidence_runner
from investigation_orchestrator.mcp_clients import (
    PeerMcpError,
    WorkloadRuntimeSnapshot,
    _pick_runtime_pod_for_deployment,
)
from investigation_service.models import EvidenceStepContract, StepExecutionInputs, TargetRef


def _workload_step() -> EvidenceStepContract:
    return EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect workload evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        requested_capability="workload_evidence_plane",
        preferred_mcp_server="kubernetes-mcp-server",
        preferred_tool_names=["pods_log", "resources_get", "events_list", "pods_list_in_namespace"],
        fallback_mcp_server=None,
        fallback_tool_names=[],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="target_context",
            cluster=None,
            namespace="operator-smoke",
            target="pod/crashy-abc123",
            profile="workload",
            lookback_minutes=15,
        ),
    )


def test_workload_external_step_prefers_peer_mcp(monkeypatch) -> None:
    step = _workload_step()

    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type(
            "ClientStub",
            (),
            {
                "collect_workload_runtime": lambda _self, _inputs: WorkloadRuntimeSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace="operator-smoke", kind="pod", name="crashy-abc123"),
                    object_state={"kind": "pod", "name": "crashy-abc123"},
                    events=["Warning BackOff pod/crashy-abc123"],
                    log_excerpt="panic: startup failed",
                    limitations=["peer partial: events only from namespace scope"],
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list", "pods_log"],
                )
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route is not None
    assert artifact.actual_route.source_kind == "peer_mcp"
    assert artifact.actual_route.mcp_server == "kubernetes-mcp-server"
    assert artifact.actual_route.tool_name == "resources_get"
    assert artifact.actual_route.tool_path == [
        "kubernetes-mcp-server",
        "resources_get",
        "events_list",
        "pods_log",
    ]
    assert artifact.evidence_bundle is not None
    assert "peer partial: events only from namespace scope" in artifact.evidence_bundle.limitations


def test_workload_external_step_falls_back_explicitly(monkeypatch) -> None:
    step = _workload_step()

    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type(
            "ClientStub",
            (),
            {
                "collect_workload_runtime": lambda _self, _inputs: (_ for _ in ()).throw(
                    PeerMcpError("peer unavailable")
                )
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route is not None
    assert artifact.actual_route.source_kind == "investigation_internal"
    assert artifact.actual_route.tool_name == "collect_workload_evidence"
    assert artifact.evidence_bundle is not None
    assert "peer workload MCP fallback: peer unavailable" in artifact.evidence_bundle.limitations


def test_pick_runtime_pod_for_deployment_uses_selector_not_prefix() -> None:
    deployment = {
        "metadata": {"name": "crashy"},
        "spec": {
            "selector": {
                "matchLabels": {
                    "app.kubernetes.io/name": "crashy",
                    "app.kubernetes.io/instance": "crashy",
                }
            }
        },
    }
    pods = {
        "items": [
            {
                "metadata": {
                    "name": "crashy-extra-aaa",
                    "creationTimestamp": "2026-03-09T12:02:00Z",
                    "labels": {
                        "app.kubernetes.io/name": "crashy-extra",
                        "app.kubernetes.io/instance": "crashy-extra",
                    },
                    "ownerReferences": [{"kind": "ReplicaSet", "name": "crashy-extra-7b6d9f"}],
                }
            },
            {
                "metadata": {
                    "name": "crashy-58b5897796-lckp9",
                    "creationTimestamp": "2026-03-09T12:01:00Z",
                    "labels": {
                        "app.kubernetes.io/name": "crashy",
                        "app.kubernetes.io/instance": "crashy",
                    },
                    "ownerReferences": [{"kind": "ReplicaSet", "name": "crashy-58b5897796"}],
                }
            },
        ]
    }

    assert _pick_runtime_pod_for_deployment(deployment, pods) == "crashy-58b5897796-lckp9"
