from investigation_orchestrator import evidence_runner
from investigation_orchestrator.mcp_clients import (
    NodeMetricsSnapshot,
    NodeRuntimeSnapshot,
    PeerMcpError,
    ServiceMetricsSnapshot,
    ServiceRuntimeSnapshot,
    WorkloadRuntimeSnapshot,
    _normalize_object_state,
)
from investigation_service.k8s_adapter import pick_runtime_pod_for_workload
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


def _service_step() -> EvidenceStepContract:
    return EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect service evidence",
        plane="service",
        artifact_type="evidence_bundle",
        requested_capability="service_evidence_plane",
        preferred_mcp_server="prometheus-mcp-server",
        preferred_tool_names=["execute_query", "execute_range_query"],
        fallback_mcp_server="kubernetes-mcp-server",
        fallback_tool_names=["resources_get", "events_list"],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="service_context",
            cluster=None,
            namespace="operator-smoke",
            target="service/api",
            profile="service",
            service_name="api",
            lookback_minutes=15,
        ),
    )


def _node_step() -> EvidenceStepContract:
    return EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect node evidence",
        plane="node",
        artifact_type="evidence_bundle",
        requested_capability="node_evidence_plane",
        preferred_mcp_server="prometheus-mcp-server",
        preferred_tool_names=["execute_query"],
        fallback_mcp_server="kubernetes-mcp-server",
        fallback_tool_names=["resources_get", "events_list"],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="target_context",
            cluster=None,
            target="node/worker3",
            profile="workload",
            node_name="worker3",
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


def test_service_external_step_prefers_prometheus_peer(monkeypatch) -> None:
    step = _service_step()
    monkeypatch.setattr(
        evidence_runner,
        "_prometheus_mcp_client",
        type(
            "PromClientStub",
            (),
            {
                "collect_service_metrics": lambda _self, _inputs: ServiceMetricsSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace="operator-smoke", kind="service", name="api"),
                    metrics={
                        "service_request_rate": 12.5,
                        "service_error_rate": 0.5,
                        "service_latency_p95_seconds": 1.2,
                        "prometheus_available": True,
                    },
                    limitations=[],
                    tool_path=["prometheus-mcp-server", "execute_query", "execute_query", "execute_query"],
                )
            },
        )(),
    )
    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type(
            "KubeClientStub",
            (),
            {
                "collect_service_runtime": lambda _self, _inputs: ServiceRuntimeSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace="operator-smoke", kind="service", name="api"),
                    object_state={"kind": "service", "name": "api"},
                    events=["Warning Unhealthy service/api"],
                    limitations=[],
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list"],
                )
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route is not None
    assert artifact.actual_route.source_kind == "peer_mcp"
    assert artifact.actual_route.mcp_server == "prometheus-mcp-server"
    assert artifact.actual_route.tool_name == "execute_query"
    assert artifact.evidence_bundle is not None
    assert artifact.evidence_bundle.metrics["service_error_rate"] == 0.5
    assert artifact.evidence_bundle.object_state["kind"] == "service"
    assert artifact.evidence_bundle.events == ["Warning Unhealthy service/api"]
    assert any(item.title == "High Service Latency" for item in artifact.evidence_bundle.findings)


def test_service_external_step_uses_kubernetes_peer_fallback_when_prometheus_is_empty(monkeypatch) -> None:
    step = _service_step()
    monkeypatch.setattr(
        evidence_runner,
        "_prometheus_mcp_client",
        type(
            "PromClientStub",
            (),
            {
                "collect_service_metrics": lambda _self, _inputs: ServiceMetricsSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace="operator-smoke", kind="service", name="api"),
                    metrics={
                        "service_request_rate": None,
                        "service_error_rate": None,
                        "service_latency_p95_seconds": None,
                        "prometheus_available": False,
                    },
                    limitations=["prometheus unavailable or returned no usable results"],
                    tool_path=["prometheus-mcp-server", "execute_query", "execute_query", "execute_query"],
                )
            },
        )(),
    )
    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type(
            "KubeClientStub",
            (),
            {
                "collect_service_runtime": lambda _self, _inputs: ServiceRuntimeSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace="operator-smoke", kind="service", name="api"),
                    object_state={"kind": "service", "name": "api"},
                    events=["Warning Unhealthy service/api"],
                    limitations=[],
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list"],
                )
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route is not None
    assert artifact.actual_route.mcp_server == "kubernetes-mcp-server"
    assert artifact.evidence_bundle is not None
    assert artifact.evidence_bundle.object_state["kind"] == "service"
    assert "prometheus unavailable or returned no usable results" in artifact.evidence_bundle.limitations


def test_service_external_step_falls_back_internally_after_peer_failures(monkeypatch) -> None:
    step = _service_step()
    monkeypatch.setattr(
        evidence_runner,
        "_prometheus_mcp_client",
        type(
            "PromClientStub",
            (),
            {
                "collect_service_metrics": lambda _self, _inputs: (_ for _ in ()).throw(PeerMcpError("prom down"))
            },
        )(),
    )
    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type(
            "KubeClientStub",
            (),
            {
                "collect_service_runtime": lambda _self, _inputs: (_ for _ in ()).throw(PeerMcpError("kube down"))
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route is not None
    assert artifact.actual_route.source_kind == "investigation_internal"
    assert artifact.actual_route.tool_name == "collect_service_evidence"
    assert (
        "peer service MCP fallback: prometheus peer failed: prom down; kubernetes peer fallback failed: kube down"
        in artifact.evidence_bundle.limitations
    )


def test_node_external_step_prefers_prometheus_with_kubernetes_enrichment(monkeypatch) -> None:
    step = _node_step()
    monkeypatch.setattr(
        evidence_runner,
        "_prometheus_mcp_client",
        type(
            "PromClientStub",
            (),
            {
                "collect_node_metrics": lambda _self, _inputs: NodeMetricsSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace=None, kind="node", name="worker3"),
                    metrics={
                        "node_memory_allocatable_bytes": 100.0,
                        "node_memory_working_set_bytes": 40.0,
                        "node_memory_request_bytes": 90.0,
                        "prometheus_available": True,
                    },
                    limitations=[],
                    tool_path=["prometheus-mcp-server", "execute_query", "execute_query", "execute_query"],
                )
            },
        )(),
    )
    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type(
            "KubeClientStub",
            (),
            {
                "collect_node_runtime": lambda _self, _inputs: NodeRuntimeSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace=None, kind="node", name="worker3"),
                    object_state={
                        "kind": "node",
                        "name": "worker3",
                        "conditions": [{"type": "Ready", "status": "False"}],
                    },
                    events=["Warning NodeNotReady node/worker3"],
                    limitations=[],
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list"],
                )
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route is not None
    assert artifact.actual_route.source_kind == "peer_mcp"
    assert artifact.actual_route.mcp_server == "prometheus-mcp-server"
    assert artifact.evidence_bundle is not None
    assert artifact.evidence_bundle.object_state["kind"] == "node"
    assert artifact.evidence_bundle.events == ["Warning NodeNotReady node/worker3"]
    assert any(item.title == "Node Not Ready" for item in artifact.evidence_bundle.findings)


def test_node_external_step_uses_kubernetes_peer_when_prometheus_is_empty(monkeypatch) -> None:
    step = _node_step()
    monkeypatch.setattr(
        evidence_runner,
        "_prometheus_mcp_client",
        type(
            "PromClientStub",
            (),
            {
                "collect_node_metrics": lambda _self, _inputs: NodeMetricsSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace=None, kind="node", name="worker3"),
                    metrics={
                        "node_memory_allocatable_bytes": None,
                        "node_memory_working_set_bytes": None,
                        "node_memory_request_bytes": None,
                        "prometheus_available": False,
                    },
                    limitations=["prometheus unavailable or returned no usable results"],
                    tool_path=["prometheus-mcp-server", "execute_query", "execute_query", "execute_query"],
                )
            },
        )(),
    )
    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type(
            "KubeClientStub",
            (),
            {
                "collect_node_runtime": lambda _self, _inputs: NodeRuntimeSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace=None, kind="node", name="worker3"),
                    object_state={"kind": "node", "name": "worker3", "conditions": []},
                    events=["Warning DiskPressure node/worker3"],
                    limitations=[],
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list"],
                )
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route is not None
    assert artifact.actual_route.mcp_server == "kubernetes-mcp-server"
    assert artifact.evidence_bundle is not None
    assert artifact.evidence_bundle.object_state["kind"] == "node"
    assert "prometheus unavailable or returned no usable results" in artifact.evidence_bundle.limitations


def test_node_external_step_falls_back_internally_after_peer_failures(monkeypatch) -> None:
    step = _node_step()
    monkeypatch.setattr(
        evidence_runner,
        "_prometheus_mcp_client",
        type(
            "PromClientStub",
            (),
            {
                "collect_node_metrics": lambda _self, _inputs: (_ for _ in ()).throw(PeerMcpError("prom down"))
            },
        )(),
    )
    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type(
            "KubeClientStub",
            (),
            {
                "collect_node_runtime": lambda _self, _inputs: (_ for _ in ()).throw(PeerMcpError("kube down"))
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route is not None
    assert artifact.actual_route.source_kind == "investigation_internal"
    assert artifact.actual_route.tool_name == "collect_node_evidence"
    assert (
        "peer node MCP fallback: prometheus peer failed: prom down; kubernetes peer fallback failed: kube down"
        in artifact.evidence_bundle.limitations
    )


def test_pick_runtime_pod_for_workload_uses_selector_not_prefix() -> None:
    deployment = {
        "kind": "Deployment",
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

    assert pick_runtime_pod_for_workload(deployment, pods) == "crashy-58b5897796-lckp9"


def test_pick_runtime_pod_for_workload_without_match_labels_fails_closed() -> None:
    deployment = {
        "kind": "Deployment",
        "metadata": {"name": "crashy"},
        "spec": {"selector": {}},
    }
    pods = {
        "items": [
            {
                "metadata": {
                    "name": "crashy-58b5897796-lckp9",
                    "labels": {"app.kubernetes.io/name": "crashy"},
                }
            }
        ]
    }

    assert pick_runtime_pod_for_workload(deployment, pods) is None


def test_pick_runtime_pod_for_statefulset_prefers_owned_pod() -> None:
    statefulset = {
        "kind": "StatefulSet",
        "metadata": {"name": "postgres"},
        "spec": {
            "selector": {
                "matchLabels": {
                    "app.kubernetes.io/name": "postgres",
                }
            }
        },
    }
    pods = {
        "items": [
            {
                "metadata": {
                    "name": "postgres-sidecar-0",
                    "creationTimestamp": "2026-03-09T12:02:00Z",
                    "labels": {"app.kubernetes.io/name": "postgres"},
                    "ownerReferences": [{"kind": "Job", "name": "postgres-sidecar"}],
                }
            },
            {
                "metadata": {
                    "name": "postgres-0",
                    "creationTimestamp": "2026-03-09T12:01:00Z",
                    "labels": {"app.kubernetes.io/name": "postgres"},
                    "ownerReferences": [{"kind": "StatefulSet", "name": "postgres"}],
                }
            },
        ]
    }

    assert pick_runtime_pod_for_workload(statefulset, pods) == "postgres-0"


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
