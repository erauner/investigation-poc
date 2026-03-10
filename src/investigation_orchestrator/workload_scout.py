from investigation_service.adequacy import EvidenceAdequacyAssessment, assess_workload_evidence_bundle
from investigation_service.execution_policy import bounded_exploration_policy_for_capability
from investigation_service.models import ActualRoute, EvidenceStepContract, SubmittedStepArtifact
from investigation_service.submission_materialization import materialize_workload_submission

from .mcp_clients import KubernetesMcpClient, PeerMcpError, WorkloadRuntimeSnapshot


def _peer_route(tool_path: list[str]) -> ActualRoute:
    server = tool_path[0] if tool_path else "kubernetes-mcp-server"
    tool_name = next((item for item in tool_path[1:] if item), None)
    return ActualRoute(
        source_kind="peer_mcp",
        mcp_server=server,
        tool_name=tool_name,
        tool_path=tool_path,
    )


def materialize_workload_snapshot(
    step: EvidenceStepContract,
    snapshot: WorkloadRuntimeSnapshot,
    *,
    attempted_routes: list[ActualRoute] | None = None,
    extra_limitations: list[str] | None = None,
) -> SubmittedStepArtifact:
    return materialize_workload_submission(
        step,
        target=snapshot.target,
        object_state=snapshot.object_state,
        events=snapshot.events,
        log_excerpt=snapshot.log_excerpt,
        actual_route=_peer_route(snapshot.tool_path),
        attempted_routes=attempted_routes,
        cluster_alias=snapshot.cluster_alias,
        extra_limitations=[*snapshot.limitations, *(extra_limitations or [])],
    )


def assess_materialized_workload_submission(artifact: SubmittedStepArtifact) -> EvidenceAdequacyAssessment:
    return assess_workload_evidence_bundle(bundle=artifact.evidence_bundle)


def maybe_run_bounded_workload_scout(
    step: EvidenceStepContract,
    *,
    baseline_snapshot: WorkloadRuntimeSnapshot,
    baseline_artifact: SubmittedStepArtifact,
    kubernetes_mcp_client: KubernetesMcpClient,
) -> SubmittedStepArtifact:
    policy = bounded_exploration_policy_for_capability(step.requested_capability)
    if policy is None or not policy.enabled or policy.max_additional_pods < 1 or policy.max_additional_probe_runs < 1:
        return baseline_artifact
    if baseline_snapshot.target.kind not in {"deployment", "statefulset"}:
        return baseline_artifact
    if not baseline_snapshot.runtime_pod_name:
        return baseline_artifact

    baseline_assessment = assess_materialized_workload_submission(baseline_artifact)
    if baseline_assessment.outcome not in {"weak", "contradictory"}:
        return baseline_artifact

    try:
        scout_snapshot = kubernetes_mcp_client.collect_workload_runtime(
            step.execution_inputs,
            excluded_pod_names=(baseline_snapshot.runtime_pod_name,),
        )
    except PeerMcpError as exc:
        if baseline_artifact.evidence_bundle is None:
            return baseline_artifact
        return baseline_artifact.model_copy(
            update={
                "attempted_routes": [*baseline_artifact.attempted_routes, ActualRoute(
                    source_kind="peer_mcp",
                    mcp_server=step.preferred_mcp_server or "kubernetes-mcp-server",
                    tool_name=None,
                    tool_path=[step.preferred_mcp_server or "kubernetes-mcp-server"],
                )],
                "evidence_bundle": baseline_artifact.evidence_bundle.model_copy(
                    update={
                        "limitations": sorted(
                            set([*baseline_artifact.evidence_bundle.limitations, f"bounded workload scout failed: {exc}"])
                        )
                    }
                ),
            }
        )

    scout_artifact = materialize_workload_snapshot(
        step,
        scout_snapshot,
        attempted_routes=[baseline_artifact.actual_route, *baseline_artifact.attempted_routes],
    )
    scout_assessment = assess_materialized_workload_submission(scout_artifact)
    if scout_assessment.outcome == "adequate":
        return scout_artifact

    if baseline_artifact.evidence_bundle is None:
        return baseline_artifact
    return baseline_artifact.model_copy(
        update={
            "attempted_routes": [*baseline_artifact.attempted_routes, scout_artifact.actual_route],
        }
    )
