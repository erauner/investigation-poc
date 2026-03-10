from .models import (
    ActualRoute,
    BuildInvestigationPlanRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    EvidenceStepContract,
    SubmittedStepArtifact,
)
from .tools import materialize_node_evidence, materialize_service_evidence, materialize_workload_evidence


def materialize_workload_submission(
    step: EvidenceStepContract,
    *,
    target,
    object_state: dict,
    events: list[str],
    log_excerpt: str,
    actual_route: ActualRoute,
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
    )


def materialize_service_submission(
    step: EvidenceStepContract,
    *,
    target,
    metrics: dict,
    actual_route: ActualRoute,
    object_state: dict | None = None,
    events: list[str] | None = None,
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
        cluster_alias=cluster_alias,
        extra_limitations=extra_limitations,
    )
    return SubmittedStepArtifact(
        step_id=step.step_id,
        evidence_bundle=bundle,
        actual_route=actual_route,
    )


def materialize_node_submission(
    step: EvidenceStepContract,
    *,
    target,
    metrics: dict,
    actual_route: ActualRoute,
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
    )
