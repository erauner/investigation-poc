from investigation_service.adequacy import (
    EvidenceAdequacyAssessment,
    assess_workload_evidence_bundle,
    assessment_improves,
    is_scout_candidate,
    workload_bundle_improves,
)
from investigation_service.execution_policy import bounded_exploration_policy_for_capability
from investigation_service.models import ActualRoute, EvidenceStepContract, SubmittedStepArtifact
from investigation_service.submission_materialization import materialize_workload_submission

from .mcp_clients import KubernetesMcpClient, PeerMcpError, WorkloadRuntimeSnapshot
from .state import PendingExplorationReview


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


def _scout_context(
    step: EvidenceStepContract,
    *,
    baseline_snapshot: WorkloadRuntimeSnapshot,
    baseline_artifact: SubmittedStepArtifact,
) -> tuple[EvidenceAdequacyAssessment, str] | None:
    policy = bounded_exploration_policy_for_capability(step.requested_capability)
    if policy is None or not policy.enabled or policy.max_additional_pods < 1 or policy.max_additional_probe_runs < 1:
        return None
    if baseline_snapshot.target.kind not in {"deployment", "statefulset"}:
        return None
    if not baseline_snapshot.runtime_pod_name:
        return None
    baseline_assessment = assess_materialized_workload_submission(baseline_artifact)
    if not is_scout_candidate(baseline_assessment):
        return None
    return baseline_assessment, baseline_snapshot.runtime_pod_name


def maybe_plan_workload_exploration_review(
    step: EvidenceStepContract,
    *,
    batch_id: str,
    baseline_snapshot: WorkloadRuntimeSnapshot,
    baseline_artifact: SubmittedStepArtifact,
) -> PendingExplorationReview | None:
    scout_context = _scout_context(
        step,
        baseline_snapshot=baseline_snapshot,
        baseline_artifact=baseline_artifact,
    )
    if scout_context is None:
        return None
    policy = bounded_exploration_policy_for_capability(step.requested_capability)
    if policy is None or not policy.human_review_enabled:
        return None
    baseline_assessment, runtime_pod_name = scout_context
    if baseline_assessment.outcome not in policy.human_review_outcomes:
        return None
    target = step.execution_inputs.target or baseline_snapshot.target.name
    return PendingExplorationReview(
        batch_id=batch_id,
        step=step,
        capability=step.requested_capability or "workload_evidence_plane",
        baseline_artifact=baseline_artifact,
        baseline_runtime_pod_name=runtime_pod_name,
        adequacy_outcome=baseline_assessment.outcome,
        adequacy_reasons=list(baseline_assessment.reasons),
        proposed_probe=f"Probe one additional runtime pod for {target} excluding {runtime_pod_name}.",
    )


def _failed_scout_artifact(
    step: EvidenceStepContract,
    *,
    baseline_artifact: SubmittedStepArtifact,
    error_message: str,
) -> SubmittedStepArtifact:
    if baseline_artifact.evidence_bundle is None:
        return baseline_artifact
    return baseline_artifact.model_copy(
        update={
            "attempted_routes": [
                *baseline_artifact.attempted_routes,
                ActualRoute(
                    source_kind="peer_mcp",
                    mcp_server=step.preferred_mcp_server or "kubernetes-mcp-server",
                    tool_name=None,
                    tool_path=[step.preferred_mcp_server or "kubernetes-mcp-server"],
                ),
            ],
            "evidence_bundle": baseline_artifact.evidence_bundle.model_copy(
                update={
                    "limitations": sorted(
                        set([*baseline_artifact.evidence_bundle.limitations, error_message])
                    )
                }
            ),
        }
    )


def execute_workload_exploration_review(
    review: PendingExplorationReview,
    *,
    kubernetes_mcp_client: KubernetesMcpClient,
) -> SubmittedStepArtifact:
    if review.decision != "approve":
        raise ValueError("approved workload exploration review is required before executing scout")

    baseline_artifact = review.baseline_artifact
    try:
        scout_snapshot = kubernetes_mcp_client.collect_workload_runtime(
            review.step.execution_inputs,
            excluded_pod_names=(review.baseline_runtime_pod_name,),
        )
    except PeerMcpError as exc:
        return _failed_scout_artifact(
            review.step,
            baseline_artifact=baseline_artifact,
            error_message=f"bounded workload scout failed: {exc}",
        )

    scout_artifact = materialize_workload_snapshot(
        review.step,
        scout_snapshot,
        attempted_routes=[baseline_artifact.actual_route, *baseline_artifact.attempted_routes],
    )
    baseline_assessment = assess_materialized_workload_submission(baseline_artifact)
    scout_assessment = assess_materialized_workload_submission(scout_artifact)
    if assessment_improves(baseline_assessment, scout_assessment) or workload_bundle_improves(
        baseline_artifact.evidence_bundle,
        scout_artifact.evidence_bundle,
    ):
        return scout_artifact

    if baseline_artifact.evidence_bundle is None:
        return baseline_artifact
    return baseline_artifact.model_copy(
        update={
            "attempted_routes": [*baseline_artifact.attempted_routes, scout_artifact.actual_route],
        }
    )


def skip_workload_exploration_review(review: PendingExplorationReview) -> SubmittedStepArtifact:
    if review.decision != "skip":
        raise ValueError("skip decision is required before skipping workload exploration review")

    baseline_artifact = review.baseline_artifact
    if baseline_artifact.evidence_bundle is None:
        return baseline_artifact
    note = "bounded workload scout skipped by review decision"
    return baseline_artifact.model_copy(
        update={
            "evidence_bundle": baseline_artifact.evidence_bundle.model_copy(
                update={
                    "limitations": sorted(set([*baseline_artifact.evidence_bundle.limitations, note]))
                }
            )
        }
    )


def maybe_run_bounded_workload_scout(
    step: EvidenceStepContract,
    *,
    baseline_snapshot: WorkloadRuntimeSnapshot,
    baseline_artifact: SubmittedStepArtifact,
    kubernetes_mcp_client: KubernetesMcpClient,
) -> SubmittedStepArtifact:
    scout_context = _scout_context(
        step,
        baseline_snapshot=baseline_snapshot,
        baseline_artifact=baseline_artifact,
    )
    if scout_context is None:
        return baseline_artifact
    baseline_assessment, runtime_pod_name = scout_context
    return execute_workload_exploration_review(
        PendingExplorationReview(
            batch_id="auto",
            step=step,
            capability=step.requested_capability or "workload_evidence_plane",
            baseline_artifact=baseline_artifact,
            baseline_runtime_pod_name=runtime_pod_name,
            adequacy_outcome=baseline_assessment.outcome,
            adequacy_reasons=list(baseline_assessment.reasons),
            proposed_probe="auto-approved bounded workload scout",
            decision="approve",
        ),
        kubernetes_mcp_client=kubernetes_mcp_client,
    )
