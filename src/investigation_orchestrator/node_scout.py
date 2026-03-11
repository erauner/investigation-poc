from investigation_service.adequacy import (
    EvidenceAdequacyAssessment,
    assess_bundle_for_capability,
    assessment_improves,
    bundle_improves_for_capability,
)
from investigation_service.exploration import (
    ExploratoryScoutContext,
    ScoutBudgetUsage,
    build_bounded_scout_observation,
)
from investigation_service.models import ActualRoute, EvidenceStepContract, ExplorationOutcome, SubmittedStepArtifact
from investigation_service.submission_materialization import materialize_node_submission

from .mcp_clients import KubernetesMcpClient, NodePodSummarySnapshot, PeerMcpError
from .runtime_logging import log_bounded_scout


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


def _peer_route(tool_path: list[str]) -> ActualRoute:
    server = tool_path[0] if tool_path else "kubernetes-mcp-server"
    tool_name = next((item for item in tool_path[1:] if item), None)
    return ActualRoute(
        source_kind="peer_mcp",
        mcp_server=server,
        tool_name=tool_name,
        tool_path=tool_path,
    )


def materialize_node_top_pods_snapshot(
    step: EvidenceStepContract,
    snapshot: NodePodSummarySnapshot,
    *,
    baseline_artifact: SubmittedStepArtifact,
    attempted_routes: list[ActualRoute] | None = None,
    extra_limitations: list[str] | None = None,
) -> SubmittedStepArtifact:
    bundle = baseline_artifact.evidence_bundle
    if bundle is None:
        return baseline_artifact
    return materialize_node_submission(
        step,
        target=snapshot.target,
        metrics=bundle.metrics,
        object_state={**bundle.object_state, "top_pods_by_memory_request": snapshot.top_pods_by_memory_request},
        events=bundle.events,
        actual_route=_peer_route(snapshot.tool_path),
        contributing_routes=_merged_contributing_routes(
            baseline_artifact.contributing_routes,
            [_peer_route(snapshot.tool_path)],
        ),
        attempted_routes=attempted_routes,
        cluster_alias=snapshot.cluster_alias,
        extra_limitations=[*bundle.limitations, *snapshot.limitations, *(extra_limitations or [])],
    )


def assess_materialized_node_submission(artifact: SubmittedStepArtifact) -> EvidenceAdequacyAssessment:
    return assess_bundle_for_capability("node_evidence_plane", bundle=artifact.evidence_bundle)


def maybe_run_bounded_node_scout(
    step: EvidenceStepContract,
    *,
    scout_context: ExploratoryScoutContext | None,
    baseline_artifact: SubmittedStepArtifact,
    kubernetes_mcp_client: KubernetesMcpClient,
) -> tuple[SubmittedStepArtifact, ExplorationOutcome | None]:
    if scout_context is None:
        return baseline_artifact, None
    policy = scout_context.policy
    if policy.max_additional_probe_runs < 1 or policy.max_related_pods < 1:
        return baseline_artifact, None
    if "node_top_pods" not in policy.probe_kinds:
        return baseline_artifact, None
    if baseline_artifact.evidence_bundle is None:
        return baseline_artifact, None

    baseline_assessment = scout_context.baseline_assessment
    budget_usage = ScoutBudgetUsage(
        probe_runs_used=1,
        related_pods_requested=policy.max_related_pods,
    )

    try:
        scout_snapshot = kubernetes_mcp_client.collect_node_top_pods(
            step.execution_inputs,
            limit=policy.max_related_pods,
        )
    except PeerMcpError as exc:
        log_bounded_scout(
            build_bounded_scout_observation(
                context=scout_context,
                probe_kind="node_top_pods",
                stop_reason="probe_failed",
                budget_usage=budget_usage,
            )
        )
        artifact = baseline_artifact.model_copy(
            update={
                "attempted_routes": [
                    *baseline_artifact.attempted_routes,
                    ActualRoute(
                        source_kind="peer_mcp",
                        mcp_server=step.fallback_mcp_server or "kubernetes-mcp-server",
                        tool_name=None,
                        tool_path=[step.fallback_mcp_server or "kubernetes-mcp-server"],
                    ),
                ],
                "evidence_bundle": baseline_artifact.evidence_bundle.model_copy(
                    update={
                        "limitations": sorted(
                            set([*baseline_artifact.evidence_bundle.limitations, f"bounded node scout failed: {exc}"])
                        )
                    }
                ),
            }
        )
        return artifact, _no_useful_change_outcome(step, scout_context, note="probe_failed")

    scout_artifact = materialize_node_top_pods_snapshot(
        step,
        scout_snapshot,
        baseline_artifact=baseline_artifact,
        attempted_routes=[baseline_artifact.actual_route, *baseline_artifact.attempted_routes],
    )
    scout_assessment = assess_materialized_node_submission(scout_artifact)
    if assessment_improves(baseline_assessment, scout_assessment) or bundle_improves_for_capability(
        "node_evidence_plane",
        baseline_artifact.evidence_bundle,
        scout_artifact.evidence_bundle,
    ):
        log_bounded_scout(
            build_bounded_scout_observation(
                context=scout_context,
                probe_kind="node_top_pods",
                stop_reason="probe_improved_artifact",
                budget_usage=budget_usage,
            )
        )
        return scout_artifact, _evidence_delta_outcome(step, scout_context, note="probe_improved_artifact")

    log_bounded_scout(
        build_bounded_scout_observation(
            context=scout_context,
            probe_kind="node_top_pods",
            stop_reason="probe_not_improving",
            budget_usage=budget_usage,
        )
    )
    artifact = baseline_artifact.model_copy(
        update={"attempted_routes": [*baseline_artifact.attempted_routes, scout_artifact.actual_route]}
    )
    return artifact, _no_useful_change_outcome(step, scout_context, note="probe_not_improving")


def _evidence_delta_outcome(
    step: EvidenceStepContract,
    scout_context: ExploratoryScoutContext,
    *,
    note: str,
) -> ExplorationOutcome:
    return ExplorationOutcome(
        step_id=step.step_id,
        capability=step.requested_capability,
        intent=scout_context.intent,
        outcome="evidence_delta",
        probe_kind="node_top_pods",
        notes=[note],
    )


def _no_useful_change_outcome(
    step: EvidenceStepContract,
    scout_context: ExploratoryScoutContext,
    *,
    note: str,
) -> ExplorationOutcome:
    return ExplorationOutcome(
        step_id=step.step_id,
        capability=step.requested_capability,
        intent=scout_context.intent,
        outcome="no_useful_change",
        probe_kind="node_top_pods",
        notes=[note],
    )
