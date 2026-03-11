from investigation_service.adequacy import (
    EvidenceAdequacyAssessment,
    assess_bundle_for_capability,
    assessment_improves,
    bundle_improves_for_capability,
)
from investigation_service.exploration import (
    BoundedScoutObservation,
    ExploratoryScoutContext,
    ScoutBudgetUsage,
    build_bounded_scout_observation,
    build_exploratory_scout_context,
)
from investigation_service.models import (
    ActualRoute,
    EvidenceStepContract,
    ExplorationOutcome,
    SubmittedStepArtifact,
)
from investigation_service.submission_materialization import materialize_workload_submission

from .mcp_clients import KubernetesMcpClient, PeerMcpError, WorkloadRuntimeSnapshot
from .runtime_logging import log_bounded_scout
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
    return assess_bundle_for_capability("workload_evidence_plane", bundle=artifact.evidence_bundle)


def _scout_context(
    scout_context: ExploratoryScoutContext | None,
    *,
    baseline_snapshot: WorkloadRuntimeSnapshot,
) -> tuple[EvidenceAdequacyAssessment, str] | None:
    if scout_context is None:
        return None
    policy = scout_context.policy
    if policy.max_additional_pods < 1 or policy.max_additional_probe_runs < 1:
        return None
    if "alternate_runtime_pod" not in policy.probe_kinds:
        return None
    if baseline_snapshot.target.kind not in {"deployment", "statefulset"}:
        return None
    if not baseline_snapshot.runtime_pod_name:
        return None
    return scout_context.baseline_assessment, baseline_snapshot.runtime_pod_name


def maybe_plan_workload_exploration_review(
    step: EvidenceStepContract,
    *,
    batch_id: str,
    scout_context: ExploratoryScoutContext | None,
    baseline_snapshot: WorkloadRuntimeSnapshot,
    baseline_artifact: SubmittedStepArtifact,
) -> PendingExplorationReview | None:
    review_context = _scout_context(
        scout_context,
        baseline_snapshot=baseline_snapshot,
    )
    if review_context is None or scout_context is None:
        return None
    policy = scout_context.policy
    if not policy.human_review_enabled:
        return None
    baseline_assessment, runtime_pod_name = review_context
    if baseline_assessment.outcome not in policy.human_review_outcomes:
        return None
    target = step.execution_inputs.target or baseline_snapshot.target.name
    review = PendingExplorationReview(
        batch_id=batch_id,
        step=step,
        capability=step.requested_capability or "workload_evidence_plane",
        baseline_artifact=baseline_artifact,
        baseline_runtime_pod_name=runtime_pod_name,
        adequacy_outcome=baseline_assessment.outcome,
        adequacy_reasons=list(baseline_assessment.reasons),
        proposed_probe=f"Probe one additional runtime pod for {target} excluding {runtime_pod_name}.",
        intent=scout_context.intent,
        probe_kind="alternate_runtime_pod",
    )
    log_bounded_scout(
        build_bounded_scout_observation(
            context=scout_context,
            probe_kind="alternate_runtime_pod",
            stop_reason="awaiting_review",
            budget_usage=ScoutBudgetUsage(),
        ),
        batch_id=batch_id,
    )
    return review


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
) -> tuple[SubmittedStepArtifact, ExplorationOutcome]:
    if review.decision != "approve":
        raise ValueError("approved workload exploration review is required before executing scout")

    baseline_artifact = review.baseline_artifact
    scout_context = build_exploratory_scout_context(step=review.step, artifact=baseline_artifact)
    if scout_context is None:
        log_bounded_scout(
            BoundedScoutObservation(
                capability=review.capability,
                step_id=review.step.step_id,
                plane=review.step.plane,
                probe_kind=review.probe_kind or "alternate_runtime_pod",
                baseline_outcome=review.adequacy_outcome,
                baseline_reasons=tuple(review.adequacy_reasons),
                stop_reason="review_context_not_applicable",
                budget_usage=ScoutBudgetUsage(),
            ),
            batch_id=review.batch_id,
        )
        if baseline_artifact.evidence_bundle is None:
            return baseline_artifact, _no_useful_change_outcome(review, note="review_context_not_applicable")
        artifact = baseline_artifact.model_copy(
            update={
                "evidence_bundle": baseline_artifact.evidence_bundle.model_copy(
                    update={
                        "limitations": sorted(
                            set(
                                [
                                    *baseline_artifact.evidence_bundle.limitations,
                                    "approved workload scout was skipped because the review context was no longer applicable",
                                ]
                            )
                        )
                    }
                )
            }
        )
        return artifact, _no_useful_change_outcome(review, note="review_context_not_applicable")
    budget_usage = ScoutBudgetUsage(
        probe_runs_used=1,
        additional_pods_used=1,
    )
    try:
        scout_snapshot = kubernetes_mcp_client.collect_workload_runtime(
            review.step.execution_inputs,
            excluded_pod_names=(review.baseline_runtime_pod_name,),
        )
    except PeerMcpError as exc:
        log_bounded_scout(
            build_bounded_scout_observation(
                context=scout_context,
                probe_kind=review.probe_kind or "alternate_runtime_pod",
                stop_reason="probe_failed",
                budget_usage=budget_usage,
            ),
            batch_id=review.batch_id,
        )
        artifact = _failed_scout_artifact(
            review.step,
            baseline_artifact=baseline_artifact,
            error_message=f"bounded workload scout failed: {exc}",
        )
        return artifact, _no_useful_change_outcome(review, note="probe_failed")

    scout_artifact = materialize_workload_snapshot(
        review.step,
        scout_snapshot,
        attempted_routes=[baseline_artifact.actual_route, *baseline_artifact.attempted_routes],
    )
    baseline_assessment = scout_context.baseline_assessment
    scout_assessment = assess_materialized_workload_submission(scout_artifact)
    if assessment_improves(baseline_assessment, scout_assessment) or bundle_improves_for_capability(
        "workload_evidence_plane",
        baseline_artifact.evidence_bundle,
        scout_artifact.evidence_bundle,
    ):
        log_bounded_scout(
            build_bounded_scout_observation(
                context=scout_context,
                probe_kind=review.probe_kind or "alternate_runtime_pod",
                stop_reason="probe_improved_artifact",
                budget_usage=budget_usage,
            ),
            batch_id=review.batch_id,
        )
        return scout_artifact, _evidence_delta_outcome(review, note="probe_improved_artifact")

    if baseline_artifact.evidence_bundle is None:
        return baseline_artifact, _no_useful_change_outcome(review, note="probe_not_improving")
    log_bounded_scout(
        build_bounded_scout_observation(
            context=scout_context,
            probe_kind=review.probe_kind or "alternate_runtime_pod",
            stop_reason="probe_not_improving",
            budget_usage=budget_usage,
        ),
        batch_id=review.batch_id,
    )
    artifact = baseline_artifact.model_copy(
        update={
            "attempted_routes": [*baseline_artifact.attempted_routes, scout_artifact.actual_route],
        }
    )
    return artifact, _no_useful_change_outcome(review, note="probe_not_improving")


def skip_workload_exploration_review(review: PendingExplorationReview) -> tuple[SubmittedStepArtifact, ExplorationOutcome]:
    if review.decision != "skip":
        raise ValueError("skip decision is required before skipping workload exploration review")

    baseline_artifact = review.baseline_artifact
    scout_context = build_exploratory_scout_context(step=review.step, artifact=baseline_artifact)
    if scout_context is not None:
        log_bounded_scout(
            build_bounded_scout_observation(
                context=scout_context,
                probe_kind=review.probe_kind or "alternate_runtime_pod",
                stop_reason="review_skipped",
                budget_usage=ScoutBudgetUsage(),
            ),
            batch_id=review.batch_id,
        )
    if baseline_artifact.evidence_bundle is None:
        return baseline_artifact, _no_useful_change_outcome(review, note="review_skipped")
    note = "bounded workload scout skipped by review decision"
    artifact = baseline_artifact.model_copy(
        update={
            "evidence_bundle": baseline_artifact.evidence_bundle.model_copy(
                update={
                    "limitations": sorted(set([*baseline_artifact.evidence_bundle.limitations, note]))
                }
            )
        }
    )
    return artifact, _no_useful_change_outcome(review, note="review_skipped")


def maybe_run_bounded_workload_scout(
    step: EvidenceStepContract,
    *,
    scout_context: ExploratoryScoutContext | None,
    baseline_snapshot: WorkloadRuntimeSnapshot,
    baseline_artifact: SubmittedStepArtifact,
    kubernetes_mcp_client: KubernetesMcpClient,
) -> tuple[SubmittedStepArtifact, ExplorationOutcome | None]:
    review_context = _scout_context(
        scout_context,
        baseline_snapshot=baseline_snapshot,
    )
    if review_context is None:
        return baseline_artifact, None
    baseline_assessment, runtime_pod_name = review_context
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
            intent=scout_context.intent,
            probe_kind="alternate_runtime_pod",
            decision="approve",
        ),
        kubernetes_mcp_client=kubernetes_mcp_client,
    )


def _evidence_delta_outcome(review: PendingExplorationReview, *, note: str) -> ExplorationOutcome:
    return ExplorationOutcome(
        step_id=review.step.step_id,
        capability=review.capability,
        intent=review.intent,
        outcome="evidence_delta",
        probe_kind=review.probe_kind,
        notes=[note],
    )


def _no_useful_change_outcome(review: PendingExplorationReview, *, note: str) -> ExplorationOutcome:
    return ExplorationOutcome(
        step_id=review.step.step_id,
        capability=review.capability,
        intent=review.intent,
        outcome="no_useful_change",
        probe_kind=review.probe_kind,
        notes=[note],
    )
