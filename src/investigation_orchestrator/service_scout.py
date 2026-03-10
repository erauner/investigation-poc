from investigation_service.adequacy import (
    EvidenceAdequacyAssessment,
    assess_service_evidence_bundle,
    assessment_improves,
    is_scout_candidate,
    service_bundle_improves,
)
from investigation_service.execution_policy import bounded_exploration_policy_for_capability
from investigation_service.models import ActualRoute, EvidenceStepContract, SubmittedStepArtifact
from investigation_service.submission_materialization import materialize_service_submission

from .mcp_clients import PeerMcpError, PrometheusMcpClient, ServiceMetricsSnapshot


def _peer_route(tool_path: list[str]) -> ActualRoute:
    server = tool_path[0] if tool_path else "prometheus-mcp-server"
    tool_name = next((item for item in tool_path[1:] if item), None)
    return ActualRoute(
        source_kind="peer_mcp",
        mcp_server=server,
        tool_name=tool_name,
        tool_path=tool_path,
    )


def materialize_service_metrics_snapshot(
    step: EvidenceStepContract,
    metrics_snapshot: ServiceMetricsSnapshot,
    *,
    baseline_artifact: SubmittedStepArtifact,
    attempted_routes: list[ActualRoute] | None = None,
    extra_limitations: list[str] | None = None,
) -> SubmittedStepArtifact:
    bundle = baseline_artifact.evidence_bundle
    if bundle is None:
        return baseline_artifact
    retained_limitations = _retained_service_limitations(bundle.limitations, metrics_snapshot.metrics)
    return materialize_service_submission(
        step,
        target=metrics_snapshot.target,
        metrics=metrics_snapshot.metrics,
        actual_route=_peer_route(metrics_snapshot.tool_path),
        attempted_routes=attempted_routes,
        object_state=bundle.object_state,
        events=bundle.events,
        cluster_alias=metrics_snapshot.cluster_alias,
        extra_limitations=[*retained_limitations, *metrics_snapshot.limitations, *(extra_limitations or [])],
    )


def _retained_service_limitations(
    baseline_limitations: list[str],
    recovered_metrics: dict[str, object],
) -> list[str]:
    retained: list[str] = []
    prometheus_available = bool(recovered_metrics.get("prometheus_available"))
    for limitation in baseline_limitations:
        if limitation == "prometheus unavailable or returned no usable results" and prometheus_available:
            continue
        if limitation.startswith("prometheus peer failed:") and prometheus_available:
            continue
        if limitation.startswith("metric unavailable: "):
            metric_key = limitation.removeprefix("metric unavailable: ").strip()
            if recovered_metrics.get(metric_key) is not None:
                continue
        retained.append(limitation)
    return retained


def assess_materialized_service_submission(artifact: SubmittedStepArtifact) -> EvidenceAdequacyAssessment:
    return assess_service_evidence_bundle(bundle=artifact.evidence_bundle)


def maybe_run_bounded_service_follow_up_scout(
    step: EvidenceStepContract,
    *,
    baseline_artifact: SubmittedStepArtifact,
    prometheus_mcp_client: PrometheusMcpClient,
) -> SubmittedStepArtifact:
    if step.step_id != "collect-service-follow-up-evidence":
        return baseline_artifact

    policy = bounded_exploration_policy_for_capability(step.requested_capability)
    if policy is None or not policy.enabled or policy.max_additional_probe_runs < 1 or policy.max_metric_families < 1:
        return baseline_artifact
    if baseline_artifact.evidence_bundle is None:
        return baseline_artifact

    baseline_assessment = assess_materialized_service_submission(baseline_artifact)
    if not is_scout_candidate(baseline_assessment):
        return baseline_artifact

    try:
        metrics_snapshot = prometheus_mcp_client.collect_service_range_metrics(
            step.execution_inputs,
            max_metric_families=policy.max_metric_families,
        )
    except PeerMcpError as exc:
        return baseline_artifact.model_copy(
            update={
                "attempted_routes": [
                    *baseline_artifact.attempted_routes,
                    ActualRoute(
                        source_kind="peer_mcp",
                        mcp_server=step.preferred_mcp_server or "prometheus-mcp-server",
                        tool_name=None,
                        tool_path=[step.preferred_mcp_server or "prometheus-mcp-server"],
                    ),
                ],
                "evidence_bundle": baseline_artifact.evidence_bundle.model_copy(
                    update={
                        "limitations": sorted(
                            set([*baseline_artifact.evidence_bundle.limitations, f"bounded service scout failed: {exc}"])
                        )
                    }
                ),
            }
        )

    scout_artifact = materialize_service_metrics_snapshot(
        step,
        metrics_snapshot,
        baseline_artifact=baseline_artifact,
        attempted_routes=[baseline_artifact.actual_route, *baseline_artifact.attempted_routes],
    )
    scout_assessment = assess_materialized_service_submission(scout_artifact)
    if assessment_improves(baseline_assessment, scout_assessment) or service_bundle_improves(
        baseline_artifact.evidence_bundle,
        scout_artifact.evidence_bundle,
    ):
        return scout_artifact

    return baseline_artifact.model_copy(
        update={"attempted_routes": [*baseline_artifact.attempted_routes, scout_artifact.actual_route]}
    )
