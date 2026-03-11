from dataclasses import dataclass, field

from investigation_service.models import (
    ActualRoute,
    ActiveEvidenceBatchContract,
    EvidenceStepContract,
    SubmittedStepArtifact,
)
from investigation_service.exploration import build_exploratory_scout_context
from investigation_service.submission_materialization import (
    materialize_attempt_only_submission,
    materialize_node_submission,
    materialize_service_submission,
)
from .mcp_clients import KubernetesMcpClient, PeerMcpError, PrometheusMcpClient
from .node_scout import maybe_run_bounded_node_scout
from .service_scout import maybe_run_bounded_service_follow_up_scout
from .state import PendingExplorationReview
from .workload_scout import (
    execute_workload_exploration_review,
    materialize_workload_snapshot,
    maybe_plan_workload_exploration_review,
    maybe_run_bounded_workload_scout,
    skip_workload_exploration_review,
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


def _merged_contributing_routes(*route_groups: list[ActualRoute]) -> list[ActualRoute]:
    merged: list[ActualRoute] = []
    seen: set[tuple[str, str | None, str | None, tuple[str, ...]]] = set()
    for group in route_groups:
        for route in group:
            key = (
                route.source_kind,
                route.mcp_server,
                route.tool_name,
                tuple(route.tool_path),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(route)
    return merged


_kubernetes_mcp_client = KubernetesMcpClient()
_prometheus_mcp_client = PrometheusMcpClient()


@dataclass(frozen=True)
class ExternalStepCollectionResult:
    submitted_steps: list[SubmittedStepArtifact]
    pending_exploration_review: PendingExplorationReview | None = None
    deferred_external_steps: tuple[EvidenceStepContract, ...] = field(default_factory=tuple)


def _workload_submission_via_peer_mcp(
    step: EvidenceStepContract,
    *,
    batch_id: str | None = None,
    allow_exploration_review: bool = False,
) -> ExternalStepCollectionResult:
    snapshot = _kubernetes_mcp_client.collect_workload_runtime(step.execution_inputs)
    baseline_artifact = materialize_workload_snapshot(
        step,
        snapshot,
    )
    scout_context = build_exploratory_scout_context(step=step, artifact=baseline_artifact)
    if allow_exploration_review and batch_id is not None:
        pending_review = maybe_plan_workload_exploration_review(
            step,
            batch_id=batch_id,
            scout_context=scout_context,
            baseline_snapshot=snapshot,
            baseline_artifact=baseline_artifact,
        )
        if pending_review is not None:
            return ExternalStepCollectionResult(
                submitted_steps=[],
                pending_exploration_review=pending_review,
            )
    return ExternalStepCollectionResult(
        submitted_steps=[
            maybe_run_bounded_workload_scout(
                step,
                scout_context=scout_context,
                baseline_snapshot=snapshot,
                baseline_artifact=baseline_artifact,
                kubernetes_mcp_client=_kubernetes_mcp_client,
            )
        ]
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
        baseline_artifact = materialize_service_submission(
            step,
            target=runtime_snapshot.target,
            metrics={"prometheus_available": False},
            object_state=runtime_snapshot.object_state,
            events=runtime_snapshot.events,
            actual_route=_peer_route(runtime_snapshot.tool_path),
            contributing_routes=[_peer_route(runtime_snapshot.tool_path)],
            attempted_routes=[_planned_peer_route(step)],
            cluster_alias=runtime_snapshot.cluster_alias,
            extra_limitations=[f"prometheus peer failed: {prom_exc}", *runtime_snapshot.limitations],
        )
        scout_context = build_exploratory_scout_context(step=step, artifact=baseline_artifact)
        return maybe_run_bounded_service_follow_up_scout(
            step,
            scout_context=scout_context,
            baseline_artifact=baseline_artifact,
            prometheus_mcp_client=_prometheus_mcp_client,
        )

    if metrics_snapshot.metrics.get("prometheus_available"):
        try:
            runtime_snapshot = _kubernetes_mcp_client.collect_service_runtime(step.execution_inputs)
        except PeerMcpError as kube_exc:
            baseline_artifact = materialize_service_submission(
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
                contributing_routes=[_peer_route(metrics_snapshot.tool_path)],
                attempted_routes=[
                    _peer_route(metrics_snapshot.tool_path),
                    _planned_fallback_peer_route(step),
                ],
                cluster_alias=metrics_snapshot.cluster_alias,
                extra_limitations=[*metrics_snapshot.limitations, f"kubernetes peer fallback failed: {kube_exc}"],
            )
            scout_context = build_exploratory_scout_context(step=step, artifact=baseline_artifact)
            return maybe_run_bounded_service_follow_up_scout(
                step,
                scout_context=scout_context,
                baseline_artifact=baseline_artifact,
                prometheus_mcp_client=_prometheus_mcp_client,
            )
        limitations = [*metrics_snapshot.limitations, *runtime_snapshot.limitations]
        baseline_artifact = materialize_service_submission(
            step,
            target=runtime_snapshot.target,
            metrics=metrics_snapshot.metrics,
            object_state=runtime_snapshot.object_state,
            events=runtime_snapshot.events,
            actual_route=_peer_route(metrics_snapshot.tool_path),
            contributing_routes=_merged_contributing_routes(
                [_peer_route(metrics_snapshot.tool_path)],
                [_peer_route(runtime_snapshot.tool_path)],
            ),
            cluster_alias=runtime_snapshot.cluster_alias,
            extra_limitations=limitations,
        )
        scout_context = build_exploratory_scout_context(step=step, artifact=baseline_artifact)
        return maybe_run_bounded_service_follow_up_scout(
            step,
            scout_context=scout_context,
            baseline_artifact=baseline_artifact,
            prometheus_mcp_client=_prometheus_mcp_client,
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
    baseline_artifact = materialize_service_submission(
        step,
        target=runtime_snapshot.target,
        metrics=metrics_snapshot.metrics,
        object_state=runtime_snapshot.object_state,
        events=runtime_snapshot.events,
        actual_route=_peer_route(runtime_snapshot.tool_path),
        contributing_routes=_merged_contributing_routes(
            [_peer_route(metrics_snapshot.tool_path)],
            [_peer_route(runtime_snapshot.tool_path)],
        ),
        attempted_routes=[_peer_route(metrics_snapshot.tool_path)],
        cluster_alias=runtime_snapshot.cluster_alias,
        extra_limitations=limitations,
    )
    scout_context = build_exploratory_scout_context(step=step, artifact=baseline_artifact)
    return maybe_run_bounded_service_follow_up_scout(
        step,
        scout_context=scout_context,
        baseline_artifact=baseline_artifact,
        prometheus_mcp_client=_prometheus_mcp_client,
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
        baseline_artifact = materialize_node_submission(
            step,
            target=runtime_snapshot.target,
            metrics={"prometheus_available": False},
            object_state=runtime_snapshot.object_state,
            events=runtime_snapshot.events,
            actual_route=_peer_route(runtime_snapshot.tool_path),
            contributing_routes=[_peer_route(runtime_snapshot.tool_path)],
            attempted_routes=[_planned_peer_route(step)],
            cluster_alias=runtime_snapshot.cluster_alias,
            extra_limitations=[f"prometheus peer failed: {prom_exc}", *runtime_snapshot.limitations],
        )
        scout_context = build_exploratory_scout_context(step=step, artifact=baseline_artifact)
        return maybe_run_bounded_node_scout(
            step,
            scout_context=scout_context,
            baseline_artifact=baseline_artifact,
            kubernetes_mcp_client=_kubernetes_mcp_client,
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
        else _peer_route(metrics_snapshot.tool_path)
    )
    baseline_artifact = materialize_node_submission(
        step,
        target=runtime_snapshot.target,
        metrics=metrics_snapshot.metrics,
        object_state=runtime_snapshot.object_state,
        events=runtime_snapshot.events,
        actual_route=actual_route,
        contributing_routes=_merged_contributing_routes(
            [_peer_route(metrics_snapshot.tool_path)] if metrics_snapshot.metrics.get("prometheus_available") else [],
            [_peer_route(runtime_snapshot.tool_path)],
        ),
        attempted_routes=[] if metrics_snapshot.metrics.get("prometheus_available") else [_peer_route(metrics_snapshot.tool_path)],
        cluster_alias=runtime_snapshot.cluster_alias,
        extra_limitations=limitations,
    )
    scout_context = build_exploratory_scout_context(step=step, artifact=baseline_artifact)
    return maybe_run_bounded_node_scout(
        step,
        scout_context=scout_context,
        baseline_artifact=baseline_artifact,
        kubernetes_mcp_client=_kubernetes_mcp_client,
    )


def _submitted_artifact(step: EvidenceStepContract) -> SubmittedStepArtifact | None:
    if step.requested_capability == "workload_evidence_plane":
        try:
            result = _workload_submission_via_peer_mcp(step)
        except PeerMcpError as exc:
            return materialize_attempt_only_submission(
                step,
                actual_route=_planned_peer_route(step),
                limitations=[f"peer workload MCP attempt failed: {exc}"],
            )
        if result.pending_exploration_review is not None:
            raise ValueError("workload review planning is only supported through collect_external_steps")
        return result.submitted_steps[0] if result.submitted_steps else None
    if step.requested_capability == "service_evidence_plane":
        return _service_submission_via_peer_mcp(step)
    if step.requested_capability == "node_evidence_plane":
        return _node_submission_via_peer_mcp(step)
    raise ValueError(f"unsupported external step capability: {step.requested_capability}")


def collect_external_steps(
    active_batch: ActiveEvidenceBatchContract,
    *,
    allow_exploration_review: bool = False,
    steps: list[EvidenceStepContract] | None = None,
) -> ExternalStepCollectionResult:
    submissions: list[SubmittedStepArtifact] = []
    pending_review: PendingExplorationReview | None = None
    external_steps = steps or [step for step in active_batch.steps if step.execution_mode == "external_preferred"]
    skipped_workload_steps = 0

    for index, step in enumerate(external_steps):
        if step.requested_capability == "workload_evidence_plane":
            try:
                result = _workload_submission_via_peer_mcp(
                    step,
                    batch_id=active_batch.batch_id,
                    allow_exploration_review=allow_exploration_review and pending_review is None,
                )
            except PeerMcpError as exc:
                submissions.append(
                    materialize_attempt_only_submission(
                        step,
                        actual_route=_planned_peer_route(step),
                        limitations=[f"peer workload MCP attempt failed: {exc}"],
                    )
                )
                continue
            submissions.extend(result.submitted_steps)
            if result.pending_exploration_review is not None and pending_review is None:
                pending_review = result.pending_exploration_review
                return ExternalStepCollectionResult(
                    submitted_steps=submissions,
                    pending_exploration_review=pending_review,
                    deferred_external_steps=tuple(external_steps[index + 1 :]),
                )
            elif not result.submitted_steps and result.pending_exploration_review is None:
                skipped_workload_steps += 1
            continue

        artifact = _submitted_artifact(step)
        if artifact is None:
            raise ValueError(f"external step {step.step_id} did not materialize an artifact")
        submissions.append(artifact)

    if external_steps and not submissions and pending_review is None and skipped_workload_steps != len(external_steps):
        raise ValueError("active batch requires external steps but none were materialized")
    return ExternalStepCollectionResult(
        submitted_steps=submissions,
        pending_exploration_review=pending_review,
    )


def apply_pending_exploration_review(review: PendingExplorationReview) -> SubmittedStepArtifact:
    if review.capability != "workload_evidence_plane":
        raise ValueError(f"unsupported exploration review capability: {review.capability}")
    if review.decision == "approve":
        return execute_workload_exploration_review(
            review,
            kubernetes_mcp_client=_kubernetes_mcp_client,
        )
    if review.decision == "skip":
        return skip_workload_exploration_review(review)
    raise ValueError("exploration review decision must be applied before execution")


def run_required_external_steps(active_batch: ActiveEvidenceBatchContract) -> list[SubmittedStepArtifact]:
    result = collect_external_steps(active_batch, allow_exploration_review=False)
    if result.pending_exploration_review is not None:
        raise ValueError("unexpected pending exploration review without review-enabled collection")
    return result.submitted_steps
