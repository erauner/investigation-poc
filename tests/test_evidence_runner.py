import json
import logging

from investigation_orchestrator import evidence_runner
from investigation_orchestrator.mcp_clients import (
    NodePodSummarySnapshot,
    NodeMetricsSnapshot,
    NodeRuntimeSnapshot,
    LokiLogsSnapshot,
    PeerMcpError,
    ServiceMetricsSnapshot,
    ServiceRuntimeSnapshot,
    WorkloadRuntimeSnapshot,
    _peer_prometheus_routing_unsupported,
    _normalize_object_state,
)
import pytest


def _bounded_scout_summaries(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    marker = "orchestrator_bounded_scout summary="
    for record in caplog.records:
        if record.name != "investigation_orchestrator.runtime":
            continue
        if marker not in record.message:
            continue
        summaries.append(json.loads(record.message.split(marker, 1)[1]))
    return summaries
from investigation_service.cluster_registry import ResolvedCluster
from investigation_service.k8s_adapter import pick_runtime_pod_for_workload
from investigation_service.models import (
    ActiveEvidenceBatchContract,
    EvidenceStepContract,
    ExplorationOutcome,
    InvestigationSubject,
    InvestigationTarget,
    StepExecutionInputs,
    TargetRef,
)


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
        fallback_tool_names=["resources_get", "events_list", "pods_list_in_namespace"],
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


def _service_follow_up_step() -> EvidenceStepContract:
    return _service_step().model_copy(
        update={
            "step_id": "collect-service-follow-up-evidence",
            "title": "Collect service follow-up evidence",
            "exploration_intent": "evidence_expansion",
        }
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
                    runtime_pod_name="crashy-abc123",
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


def test_workload_external_step_records_failed_peer_attempt_for_downstream_fallback(monkeypatch) -> None:
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

    assert artifact is not None
    assert artifact.actual_route.source_kind == "peer_mcp"
    assert artifact.actual_route.mcp_server == "kubernetes-mcp-server"
    assert artifact.actual_route.tool_name is None
    assert artifact.actual_route.tool_path == ["kubernetes-mcp-server"]
    assert artifact.evidence_bundle is None
    assert "peer workload MCP attempt failed: peer unavailable" in artifact.limitations


def test_workload_external_step_runs_bounded_scout_and_keeps_improved_artifact(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    step = _workload_step().model_copy(
        update={
            "execution_inputs": _workload_step().execution_inputs.model_copy(
                update={"target": "deployment/crashy"}
            )
        }
    )
    calls: list[tuple[str, tuple[str, ...]]] = []
    caplog.set_level(logging.INFO, logger="investigation_orchestrator.runtime")

    def _collect(_self, _inputs, *, excluded_pod_names=()):
        calls.append((_inputs.target or "", excluded_pod_names))
        if not excluded_pod_names:
            return WorkloadRuntimeSnapshot(
                cluster_alias="erauner-home",
                target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
                object_state={
                    "kind": "deployment",
                    "name": "crashy",
                    "namespace": "operator-smoke",
                    "runtimePod": {"name": "crashy-a"},
                    "readyReplicas": 1,
                    "replicas": 1,
                },
                events=["Normal ScalingReplicaSet deployment/crashy"],
                log_excerpt="",
                limitations=[],
                tool_path=["kubernetes-mcp-server", "resources_get", "events_list", "pods_list_in_namespace", "resources_get", "pods_log"],
                runtime_pod_name="crashy-a",
            )
        return WorkloadRuntimeSnapshot(
            cluster_alias="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
            object_state={
                "kind": "deployment",
                "name": "crashy",
                "namespace": "operator-smoke",
                "runtimePod": {
                    "name": "crashy-b",
                    "containers": [{"name": "app", "restartCount": 5, "ready": False}],
                },
                "readyReplicas": 0,
                "replicas": 1,
            },
            events=["Warning BackOff pod/crashy-b"],
            log_excerpt="panic: startup failed",
            limitations=[],
            tool_path=["kubernetes-mcp-server", "resources_get", "events_list", "pods_list_in_namespace", "resources_get", "pods_log"],
            runtime_pod_name="crashy-b",
        )

    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type("ClientStub", (), {"collect_workload_runtime": _collect})(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert calls == [("deployment/crashy", ()), ("deployment/crashy", ("crashy-a",))]
    assert artifact.evidence_bundle is not None
    assert any(item.title == "Crash Loop Detected" for item in artifact.evidence_bundle.findings)
    assert artifact.actual_route.tool_path[-1] == "pods_log"
    assert artifact.attempted_routes[0].tool_path[-1] == "pods_log"
    assert _bounded_scout_summaries(caplog)[-1]["stop_reason"] == "probe_improved_artifact"


def test_workload_external_step_keeps_baseline_when_scout_is_not_better(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    step = _workload_step().model_copy(
        update={
            "execution_inputs": _workload_step().execution_inputs.model_copy(
                update={"target": "deployment/crashy"}
            )
        }
    )

    caplog.set_level(logging.INFO, logger="investigation_orchestrator.runtime")

    def _collect(_self, _inputs, *, excluded_pod_names=()):
        pod_name = "crashy-a" if not excluded_pod_names else "crashy-b"
        return WorkloadRuntimeSnapshot(
            cluster_alias="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
            object_state={
                "kind": "deployment",
                "name": "crashy",
                "namespace": "operator-smoke",
                "runtimePod": {"name": pod_name},
                "readyReplicas": 1,
                "replicas": 1,
            },
            events=["Normal ScalingReplicaSet deployment/crashy"],
            log_excerpt="",
            limitations=[],
            tool_path=["kubernetes-mcp-server", "resources_get", "events_list", "pods_list_in_namespace", "resources_get", "pods_log"],
            runtime_pod_name=pod_name,
        )

    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type("ClientStub", (), {"collect_workload_runtime": _collect})(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.evidence_bundle is not None
    assert any(item.title == "No Critical Signals Found" for item in artifact.evidence_bundle.findings)
    assert artifact.attempted_routes
    assert _bounded_scout_summaries(caplog)[-1]["stop_reason"] == "probe_not_improving"


def test_workload_external_step_keeps_baseline_and_records_failed_scout(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    step = _workload_step().model_copy(
        update={
            "execution_inputs": _workload_step().execution_inputs.model_copy(
                update={"target": "deployment/crashy"}
            )
        }
    )

    caplog.set_level(logging.INFO, logger="investigation_orchestrator.runtime")

    def _collect(_self, _inputs, *, excluded_pod_names=()):
        if excluded_pod_names:
            raise PeerMcpError("no sibling pod available")
        return WorkloadRuntimeSnapshot(
            cluster_alias="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
            object_state={
                "kind": "deployment",
                "name": "crashy",
                "namespace": "operator-smoke",
                "runtimePod": {"name": "crashy-a"},
                "readyReplicas": 1,
                "replicas": 1,
            },
            events=["Normal ScalingReplicaSet deployment/crashy"],
            log_excerpt="",
            limitations=[],
            tool_path=["kubernetes-mcp-server", "resources_get", "events_list", "pods_list_in_namespace", "resources_get", "pods_log"],
            runtime_pod_name="crashy-a",
        )

    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type("ClientStub", (), {"collect_workload_runtime": _collect})(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.evidence_bundle is not None
    assert "bounded workload scout failed: no sibling pod available" in artifact.evidence_bundle.limitations
    assert artifact.attempted_routes[0].tool_path == ["kubernetes-mcp-server"]
    summary = _bounded_scout_summaries(caplog)[-1]
    assert summary["stop_reason"] == "probe_failed"
    assert summary["additional_pods_used"] == 1


def test_workload_external_step_runs_scout_for_blocked_baseline(monkeypatch) -> None:
    step = _workload_step().model_copy(
        update={
            "execution_inputs": _workload_step().execution_inputs.model_copy(
                update={"target": "deployment/crashy"}
            )
        }
    )
    calls: list[tuple[str, tuple[str, ...]]] = []

    def _collect(_self, _inputs, *, excluded_pod_names=()):
        calls.append((_inputs.target or "", excluded_pod_names))
        if not excluded_pod_names:
            return WorkloadRuntimeSnapshot(
                cluster_alias="erauner-home",
                target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
                object_state={
                    "kind": "deployment",
                    "name": "crashy",
                    "namespace": "operator-smoke",
                    "runtimePod": {"name": "crashy-a"},
                    "readyReplicas": 1,
                    "replicas": 1,
                },
                events=[],
                log_excerpt="",
                limitations=["logs unavailable"],
                tool_path=["kubernetes-mcp-server", "resources_get", "events_list", "pods_list_in_namespace", "resources_get", "pods_log"],
                runtime_pod_name="crashy-a",
            )
        return WorkloadRuntimeSnapshot(
            cluster_alias="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
            object_state={
                "kind": "deployment",
                "name": "crashy",
                "namespace": "operator-smoke",
                "runtimePod": {
                    "name": "crashy-b",
                    "containers": [{"name": "app", "restartCount": 5, "ready": False}],
                },
                "readyReplicas": 0,
                "replicas": 1,
            },
            events=["Warning BackOff pod/crashy-b"],
            log_excerpt="panic: startup failed",
            limitations=[],
            tool_path=["kubernetes-mcp-server", "resources_get", "events_list", "pods_list_in_namespace", "resources_get", "pods_log"],
            runtime_pod_name="crashy-b",
        )

    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type("ClientStub", (), {"collect_workload_runtime": _collect})(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert calls == [("deployment/crashy", ()), ("deployment/crashy", ("crashy-a",))]
    assert artifact.evidence_bundle is not None
    assert any(item.title == "Crash Loop Detected" for item in artifact.evidence_bundle.findings)


def test_workload_external_step_replaces_with_improved_non_adequate_scout(monkeypatch) -> None:
    step = _workload_step().model_copy(
        update={
            "execution_inputs": _workload_step().execution_inputs.model_copy(
                update={"target": "deployment/crashy"}
            )
        }
    )

    def _collect(_self, _inputs, *, excluded_pod_names=()):
        if not excluded_pod_names:
            return WorkloadRuntimeSnapshot(
                cluster_alias="erauner-home",
                target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
                object_state={
                    "kind": "deployment",
                    "name": "crashy",
                    "namespace": "operator-smoke",
                    "runtimePod": {"name": "crashy-a"},
                    "readyReplicas": 1,
                    "replicas": 1,
                },
                events=[],
                log_excerpt="",
                limitations=["logs unavailable"],
                tool_path=["kubernetes-mcp-server", "resources_get", "events_list", "pods_list_in_namespace", "resources_get", "pods_log"],
                runtime_pod_name="crashy-a",
            )
        return WorkloadRuntimeSnapshot(
            cluster_alias="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
            object_state={
                "kind": "deployment",
                "name": "crashy",
                "namespace": "operator-smoke",
                "runtimePod": {
                    "name": "crashy-b",
                    "containers": [{"name": "app", "restartCount": 5, "ready": False}],
                },
                "readyReplicas": 0,
                "replicas": 1,
            },
            events=["Warning BackOff pod/crashy-b"],
            log_excerpt="panic: startup failed",
            limitations=["logs unavailable"],
            tool_path=["kubernetes-mcp-server", "resources_get", "events_list", "pods_list_in_namespace", "resources_get", "pods_log"],
            runtime_pod_name="crashy-b",
        )

    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type("ClientStub", (), {"collect_workload_runtime": _collect})(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.evidence_bundle is not None
    assert any(item.title == "Crash Loop Detected" for item in artifact.evidence_bundle.findings)
    assert "logs unavailable" in artifact.evidence_bundle.limitations


def test_collect_external_steps_returns_pending_workload_review_when_enabled(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    step = _workload_step().model_copy(
        update={
            "execution_inputs": _workload_step().execution_inputs.model_copy(
                update={"target": "deployment/crashy"}
            )
        }
    )

    caplog.set_level(logging.INFO, logger="investigation_orchestrator.runtime")

    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type(
            "ClientStub",
            (),
            {
                "collect_workload_runtime": lambda _self, _inputs: WorkloadRuntimeSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
                    object_state={
                        "kind": "deployment",
                        "name": "crashy",
                        "namespace": "operator-smoke",
                        "runtimePod": {"name": "crashy-a"},
                    },
                    events=[],
                    log_excerpt="",
                    limitations=["logs unavailable"],
                    tool_path=["kubernetes-mcp-server", "resources_get", "pods_log"],
                    runtime_pod_name="crashy-a",
                )
            },
        )(),
    )

    result = evidence_runner.collect_external_steps(
        ActiveEvidenceBatchContract(
            batch_id="batch-1",
            title="Initial evidence",
            intent="Collect workload evidence",
            subject=InvestigationSubject(
                source="alert",
                kind="alert",
                summary="Investigate PodCrashLooping",
                requested_target="pod/crashy",
                alertname="PodCrashLooping",
            ),
            canonical_target=InvestigationTarget(
                source="alert",
                scope="workload",
                cluster="erauner-home",
                namespace="operator-smoke",
                requested_target="pod/crashy",
                target="deployment/crashy",
                service_name=None,
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=[],
            ),
            steps=[step],
        ),
        allow_exploration_review=True,
    )

    assert result.submitted_steps == []
    assert result.pending_exploration_review is not None
    assert result.pending_exploration_review.batch_id == "batch-1"
    assert result.pending_exploration_review.step.step_id == "collect-target-evidence"
    assert result.pending_exploration_review.adequacy_outcome == "weak"
    assert result.pending_exploration_review.baseline_runtime_pod_name == "crashy-a"
    assert result.pending_exploration_review.probe_kind == "alternate_runtime_pod"
    summary = _bounded_scout_summaries(caplog)[-1]
    assert summary["batch_id_token"] is not None
    assert summary["probe_kind"] == "alternate_runtime_pod"
    assert summary["stop_reason"] == "awaiting_review"
    assert "batch-1" not in caplog.text
    assert "collect-target-evidence" not in caplog.text


def test_apply_pending_exploration_review_skip_keeps_baseline_with_review_note(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    step = _workload_step().model_copy(
        update={
            "execution_inputs": _workload_step().execution_inputs.model_copy(
                update={"target": "deployment/crashy"}
            )
        }
    )

    caplog.set_level(logging.INFO, logger="investigation_orchestrator.runtime")

    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type(
            "ClientStub",
            (),
            {
                "collect_workload_runtime": lambda _self, _inputs: WorkloadRuntimeSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
                    object_state={
                        "kind": "deployment",
                        "name": "crashy",
                        "namespace": "operator-smoke",
                        "runtimePod": {"name": "crashy-a"},
                    },
                    events=[],
                    log_excerpt="",
                    limitations=["logs unavailable"],
                    tool_path=["kubernetes-mcp-server", "resources_get", "pods_log"],
                    runtime_pod_name="crashy-a",
                )
            },
        )(),
    )

    result = evidence_runner.collect_external_steps(
        ActiveEvidenceBatchContract(
            batch_id="batch-1",
            title="Initial evidence",
            intent="Collect workload evidence",
            subject=InvestigationSubject(
                source="alert",
                kind="alert",
                summary="Investigate PodCrashLooping",
                requested_target="pod/crashy",
                alertname="PodCrashLooping",
            ),
            canonical_target=InvestigationTarget(
                source="alert",
                scope="workload",
                cluster="erauner-home",
                namespace="operator-smoke",
                requested_target="pod/crashy",
                target="deployment/crashy",
                service_name=None,
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=[],
            ),
            steps=[step],
        ),
        allow_exploration_review=True,
    )

    review_result = evidence_runner.apply_pending_exploration_review(
        result.pending_exploration_review.model_copy(update={"decision": "skip"})
    )
    artifact = review_result.submitted_step

    assert artifact.evidence_bundle is not None
    assert "bounded workload scout skipped by review decision" in artifact.evidence_bundle.limitations
    summary = _bounded_scout_summaries(caplog)[-1]
    assert summary["stop_reason"] == "review_skipped"
    assert summary["step_id_token"] is not None
    assert "collect-target-evidence" not in caplog.text


def test_collect_external_steps_stops_after_first_pending_review(monkeypatch) -> None:
    step_one = _workload_step().model_copy(
        update={
            "execution_inputs": _workload_step().execution_inputs.model_copy(
                update={"target": "deployment/crashy-a"}
            )
        }
    )
    step_two = _workload_step().model_copy(
        update={
            "step_id": "collect-target-evidence-2",
            "execution_inputs": _workload_step().execution_inputs.model_copy(
                update={"target": "deployment/crashy-b"}
            ),
        }
    )
    calls: list[str] = []

    def _collect(_self, inputs):
        calls.append(inputs.target or "")
        return WorkloadRuntimeSnapshot(
            cluster_alias="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
            object_state={
                "kind": "deployment",
                "name": "crashy",
                "namespace": "operator-smoke",
                "runtimePod": {"name": "crashy-a"},
            },
            events=[],
            log_excerpt="",
            limitations=["logs unavailable"],
            tool_path=["kubernetes-mcp-server", "resources_get", "pods_log"],
            runtime_pod_name="crashy-a",
        )

    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type("ClientStub", (), {"collect_workload_runtime": _collect})(),
    )

    result = evidence_runner.collect_external_steps(
        ActiveEvidenceBatchContract(
            batch_id="batch-1",
            title="Initial evidence",
            intent="Collect workload evidence",
            subject=InvestigationSubject(
                source="alert",
                kind="alert",
                summary="Investigate PodCrashLooping",
                requested_target="pod/crashy",
                alertname="PodCrashLooping",
            ),
            canonical_target=InvestigationTarget(
                source="alert",
                scope="workload",
                cluster="erauner-home",
                namespace="operator-smoke",
                requested_target="pod/crashy",
                target="deployment/crashy",
                service_name=None,
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=[],
            ),
            steps=[step_one, step_two],
        ),
        allow_exploration_review=True,
    )

    assert calls == ["deployment/crashy-a"]
    assert result.submitted_steps == []
    assert result.pending_exploration_review is not None
    assert result.pending_exploration_review.step.step_id == "collect-target-evidence"
    assert [step.step_id for step in result.deferred_external_steps] == ["collect-target-evidence-2"]


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


def test_workload_external_step_augments_logs_with_loki_without_changing_actual_route(monkeypatch) -> None:
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
                    limitations=["pod logs unavailable for target"],
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list", "pods_log"],
                    runtime_pod_name="crashy-abc123",
                )
            },
        )(),
    )
    monkeypatch.setattr(
        evidence_runner,
        "_loki_mcp_client",
        type(
            "LokiStub",
            (),
            {
                "is_configured": lambda _self: True,
                "collect_workload_logs": lambda _self, _inputs, target, runtime_pod_name: LokiLogsSnapshot(
                    cluster_alias="erauner-home",
                    target=target,
                    log_excerpt="exception: dependency unavailable",
                    tool_path=["loki-mcp-server", "loki_query"],
                ),
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route.mcp_server == "kubernetes-mcp-server"
    assert artifact.evidence_bundle is not None
    assert "[Loki logs]" in artifact.evidence_bundle.log_excerpt
    assert "pod logs unavailable for target" not in artifact.evidence_bundle.limitations
    assert any(route.mcp_server == "loki-mcp-server" for route in artifact.contributing_routes)
    assert artifact.attempted_routes == []


def test_service_external_step_augments_with_loki_logs_without_changing_metrics_first_route(monkeypatch) -> None:
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
                    object_state={
                        "kind": "service",
                        "name": "api",
                        "matchedPods": [{"name": "api-abc123"}],
                    },
                    events=["Warning Unhealthy service/api"],
                    limitations=[],
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list"],
                )
            },
        )(),
    )
    monkeypatch.setattr(
        evidence_runner,
        "_loki_mcp_client",
        type(
            "LokiStub",
            (),
            {
                "is_configured": lambda _self: True,
                "collect_service_logs": lambda _self, _inputs, target, object_state=None: LokiLogsSnapshot(
                    cluster_alias="erauner-home",
                    target=target,
                    log_excerpt="error: upstream returned 500",
                    tool_path=["loki-mcp-server", "loki_query"],
                ),
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route.mcp_server == "prometheus-mcp-server"
    assert artifact.actual_route.tool_name == "execute_query"
    assert artifact.evidence_bundle is not None
    assert artifact.evidence_bundle.log_excerpt == "error: upstream returned 500"
    assert any(item.title == "Error-like Log Patterns" for item in artifact.evidence_bundle.findings)
    assert any(route.mcp_server == "loki-mcp-server" for route in artifact.contributing_routes)


def test_service_external_step_records_loki_failure_as_attempt_only(monkeypatch) -> None:
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
    monkeypatch.setattr(
        evidence_runner,
        "_loki_mcp_client",
        type(
            "LokiStub",
            (),
            {
                "is_configured": lambda _self: True,
                "collect_service_logs": lambda _self, _inputs, target, object_state=None: (_ for _ in ()).throw(
                    PeerMcpError("loki down")
                ),
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route.mcp_server == "prometheus-mcp-server"
    assert artifact.evidence_bundle is not None
    assert artifact.evidence_bundle.log_excerpt == ""
    assert artifact.attempted_routes[-1].mcp_server == "loki-mcp-server"
    assert artifact.attempted_routes[-1].tool_path == ["loki-mcp-server"]


def test_applied_exploration_review_result_proxies_submitted_step_attributes() -> None:
    step = _workload_step()
    submitted = evidence_runner._submitted_artifact(step)
    review_result = evidence_runner.AppliedExplorationReviewResult(submitted_step=submitted)

    assert review_result.step_id == submitted.step_id


def test_collect_external_steps_replays_only_deferred_external_steps(monkeypatch) -> None:
    workload_step = _workload_step()
    step = _service_step()
    monkeypatch.setattr(
        evidence_runner,
        "_kubernetes_mcp_client",
        type(
            "KubeClientStub",
            (),
            {
                "collect_workload_runtime": lambda _self, _inputs: pytest.fail("original batch workload step should not run during deferred replay"),
                "collect_service_runtime": lambda _self, _inputs: ServiceRuntimeSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace="operator-smoke", kind="service", name="api"),
                    object_state={"kind": "service", "name": "api"},
                    events=["Warning Unhealthy service/api"],
                    limitations=[],
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list"],
                ),
            },
        )(),
    )
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
                    tool_path=["prometheus-mcp-server", "execute_query"],
                )
            },
        )(),
    )

    result = evidence_runner.collect_external_steps(
        ActiveEvidenceBatchContract(
            batch_id="batch-1",
            title="Initial evidence",
            intent="Collect workload evidence",
            subject=InvestigationSubject(
                source="alert",
                kind="alert",
                summary="Investigate PodCrashLooping",
                requested_target="pod/crashy",
                alertname="PodCrashLooping",
            ),
            canonical_target=InvestigationTarget(
                source="alert",
                scope="workload",
                cluster="erauner-home",
                namespace="operator-smoke",
                requested_target="pod/crashy",
                target="deployment/crashy",
                service_name="api",
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=[],
            ),
            steps=[workload_step],
        ),
        steps=[step],
    )

    assert result.pending_exploration_review is None
    assert result.deferred_external_steps == ()
    assert [artifact.step_id for artifact in result.submitted_steps] == [step.step_id]


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
    assert artifact.attempted_routes[0].mcp_server == "prometheus-mcp-server"
    assert artifact.evidence_bundle is not None
    assert artifact.evidence_bundle.object_state["kind"] == "service"
    assert "prometheus unavailable or returned no usable results" in artifact.evidence_bundle.limitations


def test_service_external_step_uses_kubernetes_peer_when_prometheus_hard_fails(monkeypatch) -> None:
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
                "collect_service_runtime": lambda _self, _inputs: ServiceRuntimeSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace="operator-smoke", kind="service", name="api"),
                    object_state={"kind": "service", "name": "api"},
                    events=["Warning Unhealthy service/api"],
                    limitations=["runtime data limited to namespace scope"],
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list"],
                )
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route.mcp_server == "kubernetes-mcp-server"
    assert artifact.actual_route.tool_path == ["kubernetes-mcp-server", "resources_get", "events_list"]
    assert [route.mcp_server for route in artifact.attempted_routes] == ["prometheus-mcp-server"]
    assert artifact.attempted_routes[0].tool_path == ["prometheus-mcp-server"]
    assert artifact.evidence_bundle is not None
    assert "prometheus peer failed: prom down" in artifact.evidence_bundle.limitations
    assert "runtime data limited to namespace scope" in artifact.evidence_bundle.limitations


def test_service_external_step_records_failed_peer_attempts_for_downstream_fallback(monkeypatch) -> None:
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
    assert artifact.actual_route.source_kind == "peer_mcp"
    assert artifact.actual_route.mcp_server == "prometheus-mcp-server"
    assert artifact.evidence_bundle is None
    assert artifact.attempted_routes == [
        evidence_runner.ActualRoute(
            source_kind="peer_mcp",
            mcp_server="prometheus-mcp-server",
            tool_name=None,
            tool_path=["prometheus-mcp-server"],
        ),
        evidence_runner.ActualRoute(
            source_kind="peer_mcp",
            mcp_server="kubernetes-mcp-server",
            tool_name=None,
            tool_path=["kubernetes-mcp-server"],
        ),
    ]
    assert "prometheus peer failed: prom down" in artifact.limitations
    assert "kubernetes peer fallback failed: kube down" in artifact.limitations


def test_service_external_step_records_dual_peer_attempts_when_kubernetes_enrichment_fails(monkeypatch) -> None:
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
                    tool_path=["prometheus-mcp-server", "execute_query", "execute_range_query"],
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
                "collect_service_runtime": lambda _self, _inputs: (_ for _ in ()).throw(PeerMcpError("kube down"))
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.evidence_bundle is not None
    assert artifact.evidence_bundle.metrics["service_error_rate"] == 0.5
    assert artifact.evidence_bundle.object_state["kind"] == "service"
    assert artifact.evidence_bundle.events == []
    assert artifact.actual_route.tool_path == ["prometheus-mcp-server", "execute_query", "execute_range_query"]
    assert [route.mcp_server for route in artifact.attempted_routes] == [
        "prometheus-mcp-server",
        "kubernetes-mcp-server",
    ]
    assert "kubernetes peer fallback failed: kube down" in artifact.evidence_bundle.limitations


def test_service_follow_up_step_runs_bounded_range_scout_when_baseline_is_weak(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    step = _service_follow_up_step()
    range_calls: list[int] = []
    caplog.set_level(logging.INFO, logger="investigation_orchestrator.runtime")
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
                ),
                "collect_service_range_metrics": lambda _self, _inputs, max_metric_families=0: (
                    range_calls.append(max_metric_families)
                    or ServiceMetricsSnapshot(
                        cluster_alias="erauner-home",
                        target=TargetRef(namespace="operator-smoke", kind="service", name="api"),
                        metrics={
                            "service_request_rate": 12.5,
                            "service_error_rate": 0.5,
                            "service_latency_p95_seconds": 1.2,
                            "prometheus_available": True,
                        },
                        limitations=[],
                        tool_path=["prometheus-mcp-server", "execute_range_query", "execute_range_query"],
                    )
                ),
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

    assert range_calls == [2]
    assert artifact.actual_route.tool_path == ["prometheus-mcp-server", "execute_range_query", "execute_range_query"]
    assert artifact.evidence_bundle is not None
    assert artifact.evidence_bundle.metrics["service_error_rate"] == 0.5
    assert "prometheus unavailable or returned no usable results" not in artifact.evidence_bundle.limitations
    assert artifact.attempted_routes[0].mcp_server == "kubernetes-mcp-server"
    summary = _bounded_scout_summaries(caplog)[-1]
    assert summary["stop_reason"] == "probe_improved_artifact"
    assert summary["metric_families_requested"] == 2


def test_service_follow_up_step_clears_stale_prometheus_failure_limitations_after_range_recovery(monkeypatch) -> None:
    step = _service_follow_up_step()
    monkeypatch.setattr(
        evidence_runner,
        "_prometheus_mcp_client",
        type(
            "PromClientStub",
            (),
            {
                "collect_service_metrics": lambda _self, _inputs: (_ for _ in ()).throw(PeerMcpError("prom down")),
                "collect_service_range_metrics": lambda _self, _inputs, max_metric_families=0: ServiceMetricsSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace="operator-smoke", kind="service", name="api"),
                    metrics={
                        "service_request_rate": 12.5,
                        "service_error_rate": 0.5,
                        "service_latency_p95_seconds": 1.2,
                        "prometheus_available": True,
                    },
                    limitations=[],
                    tool_path=["prometheus-mcp-server", "execute_range_query", "execute_range_query"],
                ),
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
                    limitations=["runtime data limited to namespace scope"],
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list"],
                )
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route.tool_path == ["prometheus-mcp-server", "execute_range_query", "execute_range_query"]
    assert artifact.evidence_bundle is not None
    assert "prometheus peer failed: prom down" not in artifact.evidence_bundle.limitations
    assert "prometheus unavailable or returned no usable results" not in artifact.evidence_bundle.limitations
    assert "runtime data limited to namespace scope" in artifact.evidence_bundle.limitations
    assert [route.tool_path for route in artifact.attempted_routes] == [
        ["kubernetes-mcp-server", "resources_get", "events_list"],
        ["prometheus-mcp-server"],
    ]


def test_service_follow_up_step_keeps_baseline_when_range_scout_does_not_improve(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    step = _service_follow_up_step()
    caplog.set_level(logging.INFO, logger="investigation_orchestrator.runtime")
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
                ),
                "collect_service_range_metrics": lambda _self, _inputs, max_metric_families=0: ServiceMetricsSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace="operator-smoke", kind="service", name="api"),
                    metrics={
                        "service_request_rate": None,
                        "service_error_rate": None,
                        "service_latency_p95_seconds": None,
                        "prometheus_available": False,
                    },
                    limitations=["prometheus unavailable or returned no usable results"],
                    tool_path=["prometheus-mcp-server", "execute_range_query"],
                ),
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

    assert artifact.actual_route.mcp_server == "kubernetes-mcp-server"
    assert artifact.attempted_routes[-1].mcp_server == "prometheus-mcp-server"
    assert _bounded_scout_summaries(caplog)[-1]["stop_reason"] == "probe_not_improving"


def test_service_follow_up_step_records_failed_range_scout_without_changing_shape(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    step = _service_follow_up_step()
    caplog.set_level(logging.INFO, logger="investigation_orchestrator.runtime")
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
                ),
                "collect_service_range_metrics": lambda _self, _inputs, max_metric_families=0: (_ for _ in ()).throw(
                    PeerMcpError("range scout failed")
                ),
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

    assert artifact.evidence_bundle is not None
    assert "bounded service scout failed: range scout failed" in artifact.evidence_bundle.limitations
    assert artifact.attempted_routes[-1].tool_path == ["prometheus-mcp-server"]
    assert _bounded_scout_summaries(caplog)[-1]["stop_reason"] == "probe_failed"


def test_primary_service_step_never_invokes_bounded_range_scout(monkeypatch) -> None:
    step = _service_step()
    range_calls: list[int] = []
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
                ),
                "collect_service_range_metrics": lambda _self, _inputs, max_metric_families=0: (
                    range_calls.append(max_metric_families)
                    or ServiceMetricsSnapshot(
                        cluster_alias="erauner-home",
                        target=TargetRef(namespace="operator-smoke", kind="service", name="api"),
                        metrics={},
                        limitations=[],
                        tool_path=["prometheus-mcp-server", "execute_range_query"],
                    )
                ),
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

    assert range_calls == []
    assert artifact.actual_route.tool_path == [
        "prometheus-mcp-server",
        "execute_query",
        "execute_query",
        "execute_query",
    ]
    assert [route.tool_path for route in artifact.contributing_routes] == [
        ["prometheus-mcp-server", "execute_query", "execute_query", "execute_query"],
        ["kubernetes-mcp-server", "resources_get", "events_list"],
    ]


def test_primary_service_step_uses_evidence_expansion_scout_when_baseline_is_weak(monkeypatch) -> None:
    step = _service_step()
    range_calls: list[int] = []
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
                ),
                "collect_service_range_metrics": lambda _self, _inputs, max_metric_families=0: (
                    range_calls.append(max_metric_families)
                    or ServiceMetricsSnapshot(
                        cluster_alias="erauner-home",
                        target=TargetRef(namespace="operator-smoke", kind="service", name="api"),
                        metrics={
                            "service_request_rate": 25.0,
                            "service_error_rate": 0.4,
                            "service_latency_p95_seconds": 1.1,
                            "prometheus_available": True,
                        },
                        limitations=[],
                        tool_path=["prometheus-mcp-server", "execute_range_query", "execute_range_query"],
                    )
                ),
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

    assert range_calls == [2]
    assert artifact.actual_route.tool_path == ["prometheus-mcp-server", "execute_range_query", "execute_range_query"]
    assert artifact.evidence_bundle is not None
    assert artifact.evidence_bundle.metrics["service_error_rate"] == 0.4


def test_service_follow_up_step_uses_exploration_intent_not_step_id(monkeypatch) -> None:
    step = _service_follow_up_step().model_copy(
        update={
            "step_id": "collect-service-recovery-evidence",
            "title": "Collect service recovery evidence",
        }
    )
    range_calls: list[int] = []
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
                ),
                "collect_service_range_metrics": lambda _self, _inputs, max_metric_families=0: (
                    range_calls.append(max_metric_families)
                    or ServiceMetricsSnapshot(
                        cluster_alias="erauner-home",
                        target=TargetRef(namespace="operator-smoke", kind="service", name="api"),
                        metrics={
                            "service_request_rate": 120.0,
                            "service_error_rate": 0.5,
                            "service_latency_p95_seconds": 0.8,
                            "prometheus_available": True,
                        },
                        limitations=[],
                        tool_path=["prometheus-mcp-server", "execute_range_query", "execute_range_query"],
                    )
                ),
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

    assert range_calls == [2]
    assert artifact.actual_route.tool_path == [
        "prometheus-mcp-server",
        "execute_range_query",
        "execute_range_query",
    ]
    assert [route.tool_path for route in artifact.contributing_routes] == [
        ["prometheus-mcp-server", "execute_query", "execute_query", "execute_query"],
        ["kubernetes-mcp-server", "resources_get", "events_list"],
        ["prometheus-mcp-server", "execute_range_query", "execute_range_query"],
    ]


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
                ),
                "collect_node_top_pods": lambda _self, _inputs, limit=0: NodePodSummarySnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace=None, kind="node", name="worker3"),
                    top_pods_by_memory_request=[],
                    limitations=["node workload summary unavailable"],
                    tool_path=["kubernetes-mcp-server", "resources_list"],
                ),
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route is not None
    assert artifact.actual_route.mcp_server == "kubernetes-mcp-server"
    assert artifact.attempted_routes[0].mcp_server == "prometheus-mcp-server"
    assert artifact.evidence_bundle is not None
    assert artifact.evidence_bundle.object_state["kind"] == "node"
    assert "prometheus unavailable or returned no usable results" in artifact.evidence_bundle.limitations


def test_node_external_step_uses_kubernetes_peer_when_prometheus_hard_fails(monkeypatch) -> None:
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
                "collect_node_runtime": lambda _self, _inputs: NodeRuntimeSnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace=None, kind="node", name="worker3"),
                    object_state={"kind": "node", "name": "worker3", "conditions": []},
                    events=["Warning DiskPressure node/worker3"],
                    limitations=["runtime data limited to node scope"],
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list"],
                ),
                "collect_node_top_pods": lambda _self, _inputs, limit=0: NodePodSummarySnapshot(
                    cluster_alias="erauner-home",
                    target=TargetRef(namespace=None, kind="node", name="worker3"),
                    top_pods_by_memory_request=[],
                    limitations=["node workload summary unavailable"],
                    tool_path=["kubernetes-mcp-server", "resources_list"],
                ),
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.actual_route.mcp_server == "kubernetes-mcp-server"
    assert artifact.actual_route.tool_path == ["kubernetes-mcp-server", "resources_get", "events_list"]
    assert [route.tool_path for route in artifact.contributing_routes] == [
        ["kubernetes-mcp-server", "resources_get", "events_list"],
    ]
    assert [route.tool_path for route in artifact.attempted_routes] == [
        ["prometheus-mcp-server"],
        ["kubernetes-mcp-server", "resources_list"],
    ]
    assert artifact.evidence_bundle is not None
    assert "prometheus peer failed: prom down" in artifact.evidence_bundle.limitations
    assert "runtime data limited to node scope" in artifact.evidence_bundle.limitations


def test_node_external_step_runs_bounded_node_scout_for_weak_saturation_signal(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    step = _node_step()
    scout_calls: list[int] = []
    caplog.set_level(logging.INFO, logger="investigation_orchestrator.runtime")
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
                    object_state={"kind": "node", "name": "worker3", "conditions": []},
                    events=["Warning DiskPressure node/worker3"],
                    limitations=[],
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list"],
                ),
                "collect_node_top_pods": lambda _self, _inputs, limit=0: (
                    scout_calls.append(limit)
                    or NodePodSummarySnapshot(
                        cluster_alias="erauner-home",
                        target=TargetRef(namespace=None, kind="node", name="worker3"),
                        top_pods_by_memory_request=[
                            {"namespace": "operator-smoke", "name": "api-0", "memory_request_bytes": 536870912}
                        ],
                        limitations=[],
                        tool_path=["kubernetes-mcp-server", "resources_list"],
                    )
                ),
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert scout_calls == [5]
    assert artifact.actual_route.tool_path == ["kubernetes-mcp-server", "resources_list"]
    assert [route.tool_path for route in artifact.contributing_routes] == [
        ["prometheus-mcp-server", "execute_query", "execute_query", "execute_query"],
        ["kubernetes-mcp-server", "resources_get", "events_list"],
        ["kubernetes-mcp-server", "resources_list"],
    ]
    assert artifact.evidence_bundle is not None
    assert artifact.evidence_bundle.object_state["top_pods_by_memory_request"][0]["name"] == "api-0"
    assert artifact.attempted_routes[0].mcp_server == "prometheus-mcp-server"
    summary = _bounded_scout_summaries(caplog)[-1]
    assert summary["stop_reason"] == "probe_improved_artifact"
    assert summary["related_pods_requested"] == 5


def test_node_external_step_skips_bounded_node_scout_for_adequate_baseline(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    step = _node_step()
    scout_calls: list[int] = []
    caplog.set_level(logging.INFO, logger="investigation_orchestrator.runtime")
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
                        "node_memory_working_set_bytes": 92.0,
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
                ),
                "collect_node_top_pods": lambda _self, _inputs, limit=0: scout_calls.append(limit),
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert scout_calls == []
    assert artifact.actual_route.tool_path == [
        "prometheus-mcp-server",
        "execute_query",
        "execute_query",
        "execute_query",
    ]
    assert [route.tool_path for route in artifact.contributing_routes] == [
        ["prometheus-mcp-server", "execute_query", "execute_query", "execute_query"],
        ["kubernetes-mcp-server", "resources_get", "events_list"],
    ]
    assert _bounded_scout_summaries(caplog) == []


def test_node_external_step_records_failed_bounded_node_scout_without_changing_shape(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    step = _node_step()
    caplog.set_level(logging.INFO, logger="investigation_orchestrator.runtime")
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
                    object_state={"kind": "node", "name": "worker3", "conditions": []},
                    events=["Warning DiskPressure node/worker3"],
                    limitations=[],
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list"],
                ),
                "collect_node_top_pods": lambda _self, _inputs, limit=0: (_ for _ in ()).throw(
                    PeerMcpError("node scout failed")
                ),
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.evidence_bundle is not None
    assert "bounded node scout failed: node scout failed" in artifact.evidence_bundle.limitations
    assert artifact.attempted_routes[-1].tool_path == ["kubernetes-mcp-server"]
    assert _bounded_scout_summaries(caplog)[-1]["stop_reason"] == "probe_failed"


def test_node_external_step_records_failed_peer_attempts_for_downstream_fallback(monkeypatch) -> None:
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
    assert artifact.actual_route.source_kind == "peer_mcp"
    assert artifact.actual_route.mcp_server == "prometheus-mcp-server"
    assert artifact.evidence_bundle is None
    assert artifact.attempted_routes == [
        evidence_runner.ActualRoute(
            source_kind="peer_mcp",
            mcp_server="prometheus-mcp-server",
            tool_name=None,
            tool_path=["prometheus-mcp-server"],
        ),
        evidence_runner.ActualRoute(
            source_kind="peer_mcp",
            mcp_server="kubernetes-mcp-server",
            tool_name=None,
            tool_path=["kubernetes-mcp-server"],
        ),
    ]
    assert "prometheus peer failed: prom down" in artifact.limitations
    assert "kubernetes peer fallback failed: kube down" in artifact.limitations


def test_node_external_step_records_dual_peer_attempts_when_kubernetes_enrichment_fails(monkeypatch) -> None:
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
                    tool_path=["prometheus-mcp-server", "execute_query", "execute_range_query"],
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
                "collect_node_runtime": lambda _self, _inputs: (_ for _ in ()).throw(PeerMcpError("kube down"))
            },
        )(),
    )

    artifact = evidence_runner._submitted_artifact(step)

    assert artifact.evidence_bundle is None
    assert artifact.actual_route.tool_path == ["prometheus-mcp-server", "execute_query", "execute_range_query"]
    assert artifact.contributing_routes == [artifact.actual_route]
    assert [route.mcp_server for route in artifact.attempted_routes] == [
        "prometheus-mcp-server",
        "kubernetes-mcp-server",
    ]
    assert "kubernetes peer fallback failed: kube down" in artifact.limitations


def test_external_steps_still_require_non_workload_submission(monkeypatch) -> None:
    step = _service_step()
    active_batch = type(
        "ActiveBatchStub",
        (),
        {
            "steps": [step],
        },
    )()

    monkeypatch.setattr(
        evidence_runner,
        "_submitted_artifact_and_outcome",
        lambda _step: (None, None),
    )

    try:
        evidence_runner.run_required_external_steps(active_batch)
    except ValueError as exc:
        assert "did not materialize an artifact" in str(exc)
    else:
        raise AssertionError("expected non-workload external steps to still require a submission")


def test_run_required_external_steps_preserves_exploration_outcomes(monkeypatch) -> None:
    step = _service_step()
    active_batch = type("ActiveBatchStub", (), {"steps": [step]})()
    outcome = ExplorationOutcome(
        step_id=step.step_id,
        capability=step.requested_capability,
        intent="evidence_expansion",
        outcome="no_useful_change",
        probe_kind="service_range_metrics",
        notes=["probe_not_improving"],
    )
    artifact = evidence_runner.materialize_attempt_only_submission(
        step,
        actual_route=evidence_runner._planned_peer_route(step),
        limitations=["prometheus peer failed: prom down"],
    )
    monkeypatch.setattr(
        evidence_runner,
        "collect_external_steps",
        lambda _active_batch, allow_exploration_review=False: evidence_runner.ExternalStepCollectionResult(
            submitted_steps=[artifact],
            exploration_outcomes=(outcome,),
        ),
    )

    result = evidence_runner.run_required_external_steps(active_batch)

    assert result.submitted_steps == [artifact]
    assert result.exploration_outcomes == (outcome,)


def test_node_peer_prometheus_routing_allows_default_cluster_with_prometheus_url() -> None:
    cluster = ResolvedCluster(
        alias="local-kind",
        kube_context="kind-investigation",
        kubeconfig_path="/tmp/config",
        use_in_cluster=False,
        prometheus_url="http://prometheus.kagent.svc.cluster.local:9090",
        source="default",
        allowed_namespaces=None,
    )

    assert _peer_prometheus_routing_unsupported(cluster, None) is False


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
