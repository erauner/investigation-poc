from investigation_service.models import (
    ActualRoute,
    ActiveEvidenceBatchContract,
    EvidenceStepContract,
    SubmittedStepArtifact,
)
from investigation_service.submission_materialization import (
    materialize_attempt_only_submission,
    materialize_node_submission,
    materialize_service_submission,
)
from .mcp_clients import KubernetesMcpClient, PeerMcpError, PrometheusMcpClient
from .workload_scout import materialize_workload_snapshot, maybe_run_bounded_workload_scout


def _peer_route(tool_path: list[str]) -> ActualRoute:
    server = tool_path[0] if tool_path else "kubernetes-mcp-server"
    tool_name = next((item for item in tool_path[1:] if item), None)
    return ActualRoute(
        source_kind="peer_mcp",
        mcp_server=server,
        tool_name=tool_name,
        tool_path=tool_path,
    )


def _planned_peer_route(step: EvidenceStepContract) -> ActualRoute:
    server = step.preferred_mcp_server or "kubernetes-mcp-server"
    return ActualRoute(
        source_kind="peer_mcp",
        mcp_server=server,
        tool_name=None,
        tool_path=[server],
    )


def _planned_fallback_peer_route(step: EvidenceStepContract) -> ActualRoute:
    server = step.fallback_mcp_server or step.preferred_mcp_server or "kubernetes-mcp-server"
    return ActualRoute(
        source_kind="peer_mcp",
        mcp_server=server,
        tool_name=None,
        tool_path=[server],
    )


_kubernetes_mcp_client = KubernetesMcpClient()
_prometheus_mcp_client = PrometheusMcpClient()


def _workload_submission_via_peer_mcp(step: EvidenceStepContract) -> SubmittedStepArtifact:
    snapshot = _kubernetes_mcp_client.collect_workload_runtime(step.execution_inputs)
    baseline_artifact = materialize_workload_snapshot(
        step,
        snapshot,
    )
    return maybe_run_bounded_workload_scout(
        step,
        baseline_snapshot=snapshot,
        baseline_artifact=baseline_artifact,
        kubernetes_mcp_client=_kubernetes_mcp_client,
    )


def _service_submission_via_peer_mcp(step: EvidenceStepContract) -> SubmittedStepArtifact:
    try:
        metrics_snapshot = _prometheus_mcp_client.collect_service_metrics(step.execution_inputs)
    except PeerMcpError as prom_exc:
        try:
            runtime_snapshot = _kubernetes_mcp_client.collect_service_runtime(step.execution_inputs)
        except PeerMcpError as kube_exc:
            return materialize_attempt_only_submission(
                step,
                actual_route=_planned_peer_route(step),
                attempted_routes=[_planned_peer_route(step), _planned_fallback_peer_route(step)],
                limitations=[
                    f"prometheus peer failed: {prom_exc}",
                    f"kubernetes peer fallback failed: {kube_exc}",
                ],
            )
        return materialize_service_submission(
            step,
            target=runtime_snapshot.target,
            metrics={"prometheus_available": False},
            object_state=runtime_snapshot.object_state,
            events=runtime_snapshot.events,
            actual_route=_peer_route(runtime_snapshot.tool_path),
            attempted_routes=[_planned_peer_route(step)],
            cluster_alias=runtime_snapshot.cluster_alias,
            extra_limitations=[f"prometheus peer failed: {prom_exc}", *runtime_snapshot.limitations],
        )

    if metrics_snapshot.metrics.get("prometheus_available"):
        try:
            runtime_snapshot = _kubernetes_mcp_client.collect_service_runtime(step.execution_inputs)
        except PeerMcpError as kube_exc:
            return materialize_service_submission(
                step,
                target=metrics_snapshot.target,
                metrics=metrics_snapshot.metrics,
                object_state={
                    "namespace": metrics_snapshot.target.namespace,
                    "kind": metrics_snapshot.target.kind,
                    "name": metrics_snapshot.target.name,
                },
                events=[],
                actual_route=_peer_route(metrics_snapshot.tool_path),
                attempted_routes=[
                    _peer_route(metrics_snapshot.tool_path),
                    _planned_fallback_peer_route(step),
                ],
                cluster_alias=metrics_snapshot.cluster_alias,
                extra_limitations=[*metrics_snapshot.limitations, f"kubernetes peer fallback failed: {kube_exc}"],
            )
        limitations = [*metrics_snapshot.limitations, *runtime_snapshot.limitations]
        return materialize_service_submission(
            step,
            target=runtime_snapshot.target,
            metrics=metrics_snapshot.metrics,
            object_state=runtime_snapshot.object_state,
            events=runtime_snapshot.events,
            actual_route=_peer_route([*metrics_snapshot.tool_path, *runtime_snapshot.tool_path]),
            cluster_alias=runtime_snapshot.cluster_alias,
            extra_limitations=limitations,
        )

    try:
        runtime_snapshot = _kubernetes_mcp_client.collect_service_runtime(step.execution_inputs)
    except PeerMcpError as kube_exc:
        return materialize_attempt_only_submission(
            step,
            actual_route=_peer_route(metrics_snapshot.tool_path),
            attempted_routes=[
                _peer_route(metrics_snapshot.tool_path),
                _planned_fallback_peer_route(step),
            ],
            limitations=[*metrics_snapshot.limitations, f"kubernetes peer fallback failed: {kube_exc}"],
        )
    limitations = [*metrics_snapshot.limitations, *runtime_snapshot.limitations]
    return materialize_service_submission(
        step,
        target=runtime_snapshot.target,
        metrics=metrics_snapshot.metrics,
        object_state=runtime_snapshot.object_state,
        events=runtime_snapshot.events,
        actual_route=_peer_route(runtime_snapshot.tool_path),
        attempted_routes=[_peer_route(metrics_snapshot.tool_path)],
        cluster_alias=runtime_snapshot.cluster_alias,
        extra_limitations=limitations,
    )


def _node_submission_via_peer_mcp(step: EvidenceStepContract) -> SubmittedStepArtifact:
    try:
        metrics_snapshot = _prometheus_mcp_client.collect_node_metrics(step.execution_inputs)
    except PeerMcpError as prom_exc:
        try:
            runtime_snapshot = _kubernetes_mcp_client.collect_node_runtime(step.execution_inputs)
        except PeerMcpError as kube_exc:
            return materialize_attempt_only_submission(
                step,
                actual_route=_planned_peer_route(step),
                attempted_routes=[_planned_peer_route(step), _planned_fallback_peer_route(step)],
                limitations=[
                    f"prometheus peer failed: {prom_exc}",
                    f"kubernetes peer fallback failed: {kube_exc}",
                ],
            )
        return materialize_node_submission(
            step,
            target=runtime_snapshot.target,
            metrics={"prometheus_available": False},
            object_state=runtime_snapshot.object_state,
            events=runtime_snapshot.events,
            actual_route=_peer_route(runtime_snapshot.tool_path),
            attempted_routes=[_planned_peer_route(step)],
            cluster_alias=runtime_snapshot.cluster_alias,
            extra_limitations=[f"prometheus peer failed: {prom_exc}", *runtime_snapshot.limitations],
        )

    try:
        runtime_snapshot = _kubernetes_mcp_client.collect_node_runtime(step.execution_inputs)
    except PeerMcpError as kube_exc:
        return materialize_attempt_only_submission(
            step,
            actual_route=_peer_route(metrics_snapshot.tool_path),
            attempted_routes=[
                _peer_route(metrics_snapshot.tool_path),
                _planned_fallback_peer_route(step),
            ],
            limitations=[*metrics_snapshot.limitations, f"kubernetes peer fallback failed: {kube_exc}"],
        )
    limitations = [*metrics_snapshot.limitations, *runtime_snapshot.limitations]
    actual_route = (
        _peer_route(runtime_snapshot.tool_path)
        if not metrics_snapshot.metrics.get("prometheus_available")
        else _peer_route([*metrics_snapshot.tool_path, *runtime_snapshot.tool_path])
    )
    return materialize_node_submission(
        step,
        target=runtime_snapshot.target,
        metrics=metrics_snapshot.metrics,
        object_state=runtime_snapshot.object_state,
        events=runtime_snapshot.events,
        actual_route=actual_route,
        attempted_routes=[] if metrics_snapshot.metrics.get("prometheus_available") else [_peer_route(metrics_snapshot.tool_path)],
        cluster_alias=runtime_snapshot.cluster_alias,
        extra_limitations=limitations,
    )


def _submitted_artifact(step: EvidenceStepContract) -> SubmittedStepArtifact | None:
    if step.requested_capability == "workload_evidence_plane":
        try:
            return _workload_submission_via_peer_mcp(step)
        except PeerMcpError as exc:
            return materialize_attempt_only_submission(
                step,
                actual_route=_planned_peer_route(step),
                limitations=[f"peer workload MCP attempt failed: {exc}"],
            )
    if step.requested_capability == "service_evidence_plane":
        return _service_submission_via_peer_mcp(step)
    if step.requested_capability == "node_evidence_plane":
        return _node_submission_via_peer_mcp(step)
    raise ValueError(f"unsupported external step capability: {step.requested_capability}")


def run_required_external_steps(active_batch: ActiveEvidenceBatchContract) -> list[SubmittedStepArtifact]:
    submissions = []
    external_steps = [step for step in active_batch.steps if step.execution_mode == "external_preferred"]
    skipped_workload_steps = 0

    for step in external_steps:
        artifact = _submitted_artifact(step)
        if artifact is None:
            if step.requested_capability == "workload_evidence_plane":
                skipped_workload_steps += 1
                continue
            raise ValueError(f"external step {step.step_id} did not materialize an artifact")
        submissions.append(artifact)

    if external_steps and not submissions and skipped_workload_steps != len(external_steps):
        raise ValueError("active batch requires external steps but none were materialized")
    return submissions
