from .models import (
    ActualRoute,
    BuildInvestigationPlanRequest,
    CollectAlertContextRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    EvidenceStepContract,
    SubmittedStepArtifact,
)
from .tools import (
    materialize_alert_state_evidence,
    materialize_node_evidence,
    materialize_service_evidence,
    materialize_workload_evidence,
)


def materialize_attempt_only_submission(
    step: EvidenceStepContract,
    *,
    actual_route: ActualRoute,
    limitations: list[str],
    attempted_routes: list[ActualRoute] | None = None,
) -> SubmittedStepArtifact:
    return SubmittedStepArtifact(
        step_id=step.step_id,
        actual_route=actual_route,
        contributing_routes=[actual_route],
        attempted_routes=list(attempted_routes or []),
        limitations=list(limitations),
    )


def materialize_workload_submission(
    step: EvidenceStepContract,
    *,
    target,
    object_state: dict,
    events: list[str],
    log_excerpt: str,
    actual_route: ActualRoute,
    contributing_routes: list[ActualRoute] | None = None,
    attempted_routes: list[ActualRoute] | None = None,
    cluster_alias: str | None = None,
    extra_limitations: list[str] | None = None,
) -> SubmittedStepArtifact:
    inputs = step.execution_inputs
    bundle = materialize_workload_evidence(
        BuildInvestigationPlanRequest(
            cluster=inputs.cluster,
            namespace=inputs.namespace,
            target=inputs.target or "",
            profile=inputs.profile or "workload",
            service_name=inputs.service_name,
            lookback_minutes=inputs.lookback_minutes or 15,
            alertname=inputs.alertname,
            labels=inputs.labels,
            annotations=inputs.annotations,
            node_name=inputs.node_name,
        ),
        target=target,
        object_state=object_state,
        events=events,
        log_excerpt=log_excerpt,
        cluster_alias=cluster_alias,
        extra_limitations=extra_limitations,
    )
    return SubmittedStepArtifact(
        step_id=step.step_id,
        evidence_bundle=bundle,
        actual_route=actual_route,
        contributing_routes=list(contributing_routes or [actual_route]),
        attempted_routes=list(attempted_routes or []),
    )


def materialize_alert_submission(
    step: EvidenceStepContract,
    *,
    matched_alerts: list[dict[str, object]],
    actual_route: ActualRoute,
    contributing_routes: list[ActualRoute] | None = None,
    attempted_routes: list[ActualRoute] | None = None,
    cluster_alias: str | None = None,
    extra_limitations: list[str] | None = None,
) -> SubmittedStepArtifact:
    inputs = step.execution_inputs
    req = CollectAlertContextRequest(
        alertname=inputs.alertname or "",
        labels=dict(inputs.labels),
        annotations=dict(inputs.annotations),
        cluster=inputs.cluster,
        namespace=inputs.namespace,
        node_name=inputs.node_name,
        target=inputs.target,
        profile=inputs.profile or "workload",
        service_name=inputs.service_name,
        lookback_minutes=inputs.lookback_minutes or 15,
    )
    bundle = materialize_alert_state_evidence(
        req,
        matched_alerts=matched_alerts,
        cluster_alias=cluster_alias or req.cluster or "current-context",
        extra_limitations=extra_limitations,
    )
    return SubmittedStepArtifact(
        step_id=step.step_id,
        evidence_bundle=bundle,
        actual_route=actual_route,
        contributing_routes=list(contributing_routes or [actual_route]),
        attempted_routes=list(attempted_routes or []),
    )


def materialize_service_submission(
    step: EvidenceStepContract,
    *,
    target,
    metrics: dict,
    actual_route: ActualRoute,
    contributing_routes: list[ActualRoute] | None = None,
    attempted_routes: list[ActualRoute] | None = None,
    object_state: dict | None = None,
    events: list[str] | None = None,
    log_excerpt: str = "",
    cluster_alias: str | None = None,
    extra_limitations: list[str] | None = None,
) -> SubmittedStepArtifact:
    inputs = step.execution_inputs
    bundle = materialize_service_evidence(
        CollectServiceContextRequest(
            cluster=inputs.cluster,
            namespace=inputs.namespace or "",
            service_name=inputs.service_name or target.name,
            target=inputs.target,
            lookback_minutes=inputs.lookback_minutes or 15,
        ),
        target=target,
        metrics=metrics,
        object_state=object_state,
        events=events,
        log_excerpt=log_excerpt,
        cluster_alias=cluster_alias,
        extra_limitations=extra_limitations,
    )
    return SubmittedStepArtifact(
        step_id=step.step_id,
        evidence_bundle=bundle,
        actual_route=actual_route,
        contributing_routes=list(contributing_routes or [actual_route]),
        attempted_routes=list(attempted_routes or []),
    )


def materialize_node_submission(
    step: EvidenceStepContract,
    *,
    target,
    metrics: dict,
    actual_route: ActualRoute,
    contributing_routes: list[ActualRoute] | None = None,
    attempted_routes: list[ActualRoute] | None = None,
    object_state: dict | None = None,
    events: list[str] | None = None,
    cluster_alias: str | None = None,
    extra_limitations: list[str] | None = None,
) -> SubmittedStepArtifact:
    inputs = step.execution_inputs
    bundle = materialize_node_evidence(
        CollectNodeContextRequest(
            cluster=inputs.cluster,
            node_name=inputs.node_name or target.name,
            lookback_minutes=inputs.lookback_minutes or 15,
        ),
        target=target,
        metrics=metrics,
        object_state=object_state,
        events=events,
        cluster_alias=cluster_alias,
        extra_limitations=extra_limitations,
    )
    return SubmittedStepArtifact(
        step_id=step.step_id,
        evidence_bundle=bundle,
        actual_route=actual_route,
        contributing_routes=list(contributing_routes or [actual_route]),
        attempted_routes=list(attempted_routes or []),
    )
