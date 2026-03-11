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
from investigation_service.submission_materialization import materialize_service_submission

from .mcp_clients import PeerMcpError, PrometheusMcpClient, ServiceMetricsSnapshot
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
        contributing_routes=_merged_contributing_routes(
            baseline_artifact.contributing_routes,
            [_peer_route(metrics_snapshot.tool_path)],
        ),
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
    return assess_bundle_for_capability("service_evidence_plane", bundle=artifact.evidence_bundle)


def maybe_run_bounded_service_evidence_expansion_scout(
    step: EvidenceStepContract,
    *,
    scout_context: ExploratoryScoutContext | None,
    baseline_artifact: SubmittedStepArtifact,
    prometheus_mcp_client: PrometheusMcpClient,
) -> tuple[SubmittedStepArtifact, ExplorationOutcome | None]:
    if step.exploration_intent is None:
        return baseline_artifact, None
    if scout_context is None:
        return baseline_artifact, None
    if scout_context.intent != "evidence_expansion":
        return baseline_artifact, None
    policy = scout_context.policy
    if policy.max_additional_probe_runs < 1 or policy.max_metric_families < 1:
        return baseline_artifact, None
    if "service_range_metrics" not in policy.probe_kinds:
        return baseline_artifact, None
    if baseline_artifact.evidence_bundle is None:
        return baseline_artifact, None

    baseline_assessment = scout_context.baseline_assessment
    budget_usage = ScoutBudgetUsage(
        probe_runs_used=1,
        metric_families_requested=policy.max_metric_families,
    )

    try:
        metrics_snapshot = prometheus_mcp_client.collect_service_range_metrics(
            step.execution_inputs,
            max_metric_families=policy.max_metric_families,
        )
    except PeerMcpError as exc:
        log_bounded_scout(
            build_bounded_scout_observation(
                context=scout_context,
                probe_kind="service_range_metrics",
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
        return artifact, _no_useful_change_outcome(step, scout_context, note="probe_failed")

    scout_artifact = materialize_service_metrics_snapshot(
        step,
        metrics_snapshot,
        baseline_artifact=baseline_artifact,
        attempted_routes=[baseline_artifact.actual_route, *baseline_artifact.attempted_routes],
    )
    scout_assessment = assess_materialized_service_submission(scout_artifact)
    if assessment_improves(baseline_assessment, scout_assessment) or bundle_improves_for_capability(
        "service_evidence_plane",
        baseline_artifact.evidence_bundle,
        scout_artifact.evidence_bundle,
    ):
        log_bounded_scout(
            build_bounded_scout_observation(
                context=scout_context,
                probe_kind="service_range_metrics",
                stop_reason="probe_improved_artifact",
                budget_usage=budget_usage,
            )
        )
        return scout_artifact, _evidence_delta_outcome(step, scout_context, note="probe_improved_artifact")

    log_bounded_scout(
        build_bounded_scout_observation(
            context=scout_context,
            probe_kind="service_range_metrics",
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
        probe_kind="service_range_metrics",
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
        probe_kind="service_range_metrics",
        notes=[note],
    )
