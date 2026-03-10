from investigation_service.models import (
    ActualRoute,
    ActiveEvidenceBatchContract,
    CollectContextRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    EvidenceBundle,
    EvidenceStepContract,
    SubmittedStepArtifact,
)
from investigation_service import runtime_api
from investigation_service.tools import collect_node_evidence, collect_service_evidence, collect_workload_evidence
from .mcp_clients import KubernetesMcpClient, PeerMcpError, PrometheusMcpClient


def _actual_route(tool_name: str) -> ActualRoute:
    return ActualRoute(
        source_kind="investigation_internal",
        mcp_server="investigation-mcp-server",
        tool_name=tool_name,
        tool_path=["investigation_orchestrator.evidence_runner", tool_name],
    )


def _peer_route(tool_path: list[str]) -> ActualRoute:
    server = tool_path[0] if tool_path else "kubernetes-mcp-server"
    tool_name = next((item for item in tool_path[1:] if item), None)
    return ActualRoute(
        source_kind="peer_mcp",
        mcp_server=server,
        tool_name=tool_name,
        tool_path=tool_path,
    )


_kubernetes_mcp_client = KubernetesMcpClient()
_prometheus_mcp_client = PrometheusMcpClient()


def _workload_bundle(step: EvidenceStepContract) -> EvidenceBundle:
    inputs = step.execution_inputs
    return collect_workload_evidence(
        CollectContextRequest(
            cluster=inputs.cluster,
            namespace=inputs.namespace,
            target=inputs.target or "",
            profile=inputs.profile or "workload",
            service_name=inputs.service_name,
            lookback_minutes=inputs.lookback_minutes or 15,
        )
    )


def _service_bundle(step: EvidenceStepContract) -> EvidenceBundle:
    inputs = step.execution_inputs
    return collect_service_evidence(
        CollectServiceContextRequest(
            cluster=inputs.cluster,
            namespace=inputs.namespace or "",
            service_name=inputs.service_name or "",
            target=inputs.target,
            lookback_minutes=inputs.lookback_minutes or 15,
        )
    )


def _node_bundle(step: EvidenceStepContract) -> EvidenceBundle:
    inputs = step.execution_inputs
    return collect_node_evidence(
        CollectNodeContextRequest(
            cluster=inputs.cluster,
            node_name=inputs.node_name or "",
            lookback_minutes=inputs.lookback_minutes or 15,
        )
    )


def _workload_submission_via_peer_mcp(step: EvidenceStepContract) -> SubmittedStepArtifact:
    snapshot = _kubernetes_mcp_client.collect_workload_runtime(step.execution_inputs)
    return runtime_api.materialize_workload_submission(
        step,
        target=snapshot.target,
        object_state=snapshot.object_state,
        events=snapshot.events,
        log_excerpt=snapshot.log_excerpt,
        actual_route=_peer_route(snapshot.tool_path),
        cluster_alias=snapshot.cluster_alias,
        extra_limitations=snapshot.limitations,
    )


def _workload_submission_via_internal_fallback(step: EvidenceStepContract, reason: str) -> SubmittedStepArtifact:
    bundle = _workload_bundle(step)
    limitations = sorted(set([*bundle.limitations, f"peer workload MCP fallback: {reason}"]))
    return SubmittedStepArtifact(
        step_id=step.step_id,
        evidence_bundle=bundle.model_copy(update={"limitations": limitations}),
        actual_route=_actual_route("collect_workload_evidence"),
    )


def _service_submission_via_peer_mcp(step: EvidenceStepContract) -> SubmittedStepArtifact:
    try:
        metrics_snapshot = _prometheus_mcp_client.collect_service_metrics(step.execution_inputs)
    except PeerMcpError as prom_exc:
        try:
            runtime_snapshot = _kubernetes_mcp_client.collect_service_runtime(step.execution_inputs)
        except PeerMcpError as kube_exc:
            raise PeerMcpError(
                f"prometheus peer failed: {prom_exc}; kubernetes peer fallback failed: {kube_exc}"
            ) from kube_exc
        return runtime_api.materialize_service_submission(
            step,
            target=runtime_snapshot.target,
            metrics={"prometheus_available": False},
            object_state=runtime_snapshot.object_state,
            events=runtime_snapshot.events,
            actual_route=_peer_route(runtime_snapshot.tool_path),
            cluster_alias=runtime_snapshot.cluster_alias,
            extra_limitations=[f"prometheus peer failed: {prom_exc}", *runtime_snapshot.limitations],
        )

    if metrics_snapshot.metrics.get("prometheus_available"):
        runtime_snapshot = _kubernetes_mcp_client.collect_service_runtime(step.execution_inputs)
        limitations = [*metrics_snapshot.limitations, *runtime_snapshot.limitations]
        return runtime_api.materialize_service_submission(
            step,
            target=runtime_snapshot.target,
            metrics=metrics_snapshot.metrics,
            object_state=runtime_snapshot.object_state,
            events=runtime_snapshot.events,
            actual_route=_peer_route([*metrics_snapshot.tool_path, *runtime_snapshot.tool_path]),
            cluster_alias=runtime_snapshot.cluster_alias,
            extra_limitations=limitations,
        )

    runtime_snapshot = _kubernetes_mcp_client.collect_service_runtime(step.execution_inputs)
    limitations = [*metrics_snapshot.limitations, *runtime_snapshot.limitations]
    return runtime_api.materialize_service_submission(
        step,
        target=runtime_snapshot.target,
        metrics=metrics_snapshot.metrics,
        object_state=runtime_snapshot.object_state,
        events=runtime_snapshot.events,
        actual_route=_peer_route(runtime_snapshot.tool_path),
        cluster_alias=runtime_snapshot.cluster_alias,
        extra_limitations=limitations,
    )


def _service_submission_via_internal_fallback(step: EvidenceStepContract, reason: str) -> SubmittedStepArtifact:
    bundle = _service_bundle(step)
    limitations = sorted(set([*bundle.limitations, f"peer service MCP fallback: {reason}"]))
    return SubmittedStepArtifact(
        step_id=step.step_id,
        evidence_bundle=bundle.model_copy(update={"limitations": limitations}),
        actual_route=_actual_route("collect_service_evidence"),
    )


def _submitted_artifact(step: EvidenceStepContract) -> SubmittedStepArtifact:
    if step.requested_capability == "workload_evidence_plane":
        try:
            return _workload_submission_via_peer_mcp(step)
        except PeerMcpError as exc:
            return _workload_submission_via_internal_fallback(step, str(exc))
    if step.requested_capability == "service_evidence_plane":
        try:
            return _service_submission_via_peer_mcp(step)
        except PeerMcpError as exc:
            return _service_submission_via_internal_fallback(step, str(exc))
    if step.requested_capability == "node_evidence_plane":
        return SubmittedStepArtifact(
            step_id=step.step_id,
            evidence_bundle=_node_bundle(step),
            actual_route=_actual_route("collect_node_evidence"),
        )
    raise ValueError(f"unsupported external step capability: {step.requested_capability}")


def run_required_external_steps(active_batch: ActiveEvidenceBatchContract) -> list[SubmittedStepArtifact]:
    submissions = [
        _submitted_artifact(step)
        for step in active_batch.steps
        if step.execution_mode == "external_preferred"
    ]
    if active_batch.steps and not submissions and any(
        step.execution_mode == "external_preferred" for step in active_batch.steps
    ):
        raise ValueError("active batch requires external steps but none were materialized")
    return submissions
