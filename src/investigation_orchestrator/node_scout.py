from investigation_service.adequacy import (
    EvidenceAdequacyAssessment,
    assess_node_evidence_bundle,
    assessment_improves,
    is_scout_candidate,
    node_bundle_improves,
)
from investigation_service.execution_policy import bounded_exploration_policy_for_capability
from investigation_service.models import ActualRoute, EvidenceStepContract, SubmittedStepArtifact
from investigation_service.submission_materialization import materialize_node_submission

from .mcp_clients import KubernetesMcpClient, NodePodSummarySnapshot, PeerMcpError


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
        attempted_routes=attempted_routes,
        cluster_alias=snapshot.cluster_alias,
        extra_limitations=[*bundle.limitations, *snapshot.limitations, *(extra_limitations or [])],
    )


def assess_materialized_node_submission(artifact: SubmittedStepArtifact) -> EvidenceAdequacyAssessment:
    return assess_node_evidence_bundle(bundle=artifact.evidence_bundle)


def maybe_run_bounded_node_scout(
    step: EvidenceStepContract,
    *,
    baseline_artifact: SubmittedStepArtifact,
    kubernetes_mcp_client: KubernetesMcpClient,
) -> SubmittedStepArtifact:
    policy = bounded_exploration_policy_for_capability(step.requested_capability)
    if policy is None or not policy.enabled or policy.max_additional_probe_runs < 1 or policy.max_related_pods < 1:
        return baseline_artifact
    if baseline_artifact.evidence_bundle is None:
        return baseline_artifact

    baseline_assessment = assess_materialized_node_submission(baseline_artifact)
    if not is_scout_candidate(baseline_assessment):
        return baseline_artifact

    try:
        scout_snapshot = kubernetes_mcp_client.collect_node_top_pods(
            step.execution_inputs,
            limit=policy.max_related_pods,
        )
    except PeerMcpError as exc:
        return baseline_artifact.model_copy(
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

    scout_artifact = materialize_node_top_pods_snapshot(
        step,
        scout_snapshot,
        baseline_artifact=baseline_artifact,
        attempted_routes=[baseline_artifact.actual_route, *baseline_artifact.attempted_routes],
    )
    scout_assessment = assess_materialized_node_submission(scout_artifact)
    if assessment_improves(baseline_assessment, scout_assessment) or node_bundle_improves(
        baseline_artifact.evidence_bundle,
        scout_artifact.evidence_bundle,
    ):
        return scout_artifact

    return baseline_artifact.model_copy(
        update={"attempted_routes": [*baseline_artifact.attempted_routes, scout_artifact.actual_route]}
    )
