from investigation_orchestrator import evidence_runner
from investigation_orchestrator.mcp_clients import (
    PeerMcpError,
    WorkloadRuntimeSnapshot,
    _normalize_object_state,
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


def test_normalize_object_state_for_deployment_attaches_runtime_pod() -> None:
    deployment = {
        "metadata": {
            "name": "crashy",
            "namespace": "operator-smoke",
            "creationTimestamp": "2026-03-09T12:00:00Z",
        },
        "spec": {
            "selector": {
                "matchLabels": {
                    "app.kubernetes.io/name": "crashy",
                }
            }
        },
        "status": {
            "readyReplicas": 0,
            "replicas": 1,
            "observedGeneration": 3,
        },
    }
    runtime_pod = {
        "metadata": {
            "name": "crashy-58b5897796-lckp9",
            "namespace": "operator-smoke",
            "creationTimestamp": "2026-03-09T12:01:00Z",
            "labels": {"app.kubernetes.io/name": "crashy"},
            "ownerReferences": [{"kind": "ReplicaSet", "name": "crashy-58b5897796"}],
        },
        "spec": {
            "containers": [{"name": "app", "image": "busybox:1.36"}],
        },
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "app",
                    "ready": False,
                    "restartCount": 5,
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    "lastState": {"terminated": {"reason": "Error", "exitCode": 1}},
                }
            ],
        },
    }

    normalized = _normalize_object_state(
        deployment,
        TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
        runtime_pod_raw=runtime_pod,
    )

    assert normalized["kind"] == "deployment"
    assert normalized["runtimePod"]["kind"] == "pod"
    assert normalized["runtimePod"]["name"] == "crashy-58b5897796-lckp9"
    assert normalized["runtimePod"]["containers"][0]["restartCount"] == 5


def test_normalize_object_state_for_statefulset_attaches_runtime_pod() -> None:
    statefulset = {
        "metadata": {
            "name": "postgres",
            "namespace": "operator-smoke",
            "creationTimestamp": "2026-03-09T12:00:00Z",
        },
        "spec": {
            "selector": {
                "matchLabels": {
                    "app.kubernetes.io/name": "postgres",
                }
            }
        },
        "status": {
            "readyReplicas": 0,
            "replicas": 1,
            "observedGeneration": 2,
        },
    }
    runtime_pod = {
        "metadata": {
            "name": "postgres-0",
            "namespace": "operator-smoke",
            "creationTimestamp": "2026-03-09T12:01:00Z",
            "labels": {"app.kubernetes.io/name": "postgres"},
            "ownerReferences": [{"kind": "StatefulSet", "name": "postgres"}],
        },
        "spec": {
            "containers": [{"name": "db", "image": "postgres:16"}],
        },
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "db",
                    "ready": False,
                    "restartCount": 2,
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    "lastState": {"terminated": {"reason": "Error", "exitCode": 1}},
                }
            ],
        },
    }

    normalized = _normalize_object_state(
        statefulset,
        TargetRef(namespace="operator-smoke", kind="statefulset", name="postgres"),
        runtime_pod_raw=runtime_pod,
    )

    assert normalized["kind"] == "statefulset"
    assert normalized["runtimePod"]["kind"] == "pod"
    assert normalized["runtimePod"]["name"] == "postgres-0"
    assert normalized["runtimePod"]["containers"][0]["restartCount"] == 2
