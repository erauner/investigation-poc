from investigation_service.models import (
    CollectContextRequest,
    CollectedContextResponse,
    EvidenceBundle,
    Finding,
    InvestigationTarget,
    TargetRef,
)
from investigation_service.synthesis import build_root_cause_report, synthesize_root_cause


def test_synthesize_root_cause_matches_legacy_workload_path() -> None:
    context = CollectedContextResponse(
        cluster="erauner-home",
        target=TargetRef(namespace="default", kind="pod", name="api-123"),
        object_state={"kind": "pod", "name": "api-123"},
        events=["Warning BackOff pod/api-123 Back-off restarting failed container"],
        log_excerpt="starting",
        metrics={"pod_restart_rate": 0.0034},
        findings=[
            Finding(
                severity="critical",
                source="events",
                title="Crash Loop Detected",
                evidence="Events indicate BackOff/CrashLoopBackOff behavior",
            )
        ],
        limitations=[],
        enrichment_hints=["high restart rate; fetch recent alert history or rollout events for this workload"],
    )
    bundle = EvidenceBundle(
        cluster=context.cluster,
        target=context.target,
        object_state=context.object_state,
        events=context.events,
        log_excerpt=context.log_excerpt,
        metrics=context.metrics,
        findings=context.findings,
        limitations=context.limitations,
        enrichment_hints=context.enrichment_hints,
    )
    target = InvestigationTarget(
        source="manual",
        scope="workload",
        cluster="erauner-home",
        namespace="default",
        requested_target="pod/api-123",
        target="pod/api-123",
        node_name=None,
        service_name=None,
        profile="workload",
        lookback_minutes=15,
        normalization_notes=[],
    )

    artifact_report = synthesize_root_cause(bundle, target)
    legacy_report = build_root_cause_report(
        context,
        CollectContextRequest(namespace="default", target="pod/api-123", profile="workload"),
    )

    assert artifact_report == legacy_report


def test_synthesize_root_cause_matches_legacy_service_path() -> None:
    context = CollectedContextResponse(
        cluster="erauner-home",
        target=TargetRef(namespace="kagent", kind="service", name="controller"),
        object_state={"kind": "service", "name": "controller"},
        events=["no related events"],
        log_excerpt="",
        metrics={"service_latency_p95_seconds": 1.2, "prometheus_available": True},
        findings=[
            Finding(
                severity="warning",
                source="prometheus",
                title="High Service Latency",
                evidence="p95 latency is 1.200s over the lookback window",
            )
        ],
        limitations=[],
        enrichment_hints=[],
    )
    bundle = EvidenceBundle(
        cluster=context.cluster,
        target=context.target,
        object_state=context.object_state,
        events=context.events,
        log_excerpt=context.log_excerpt,
        metrics=context.metrics,
        findings=context.findings,
        limitations=context.limitations,
        enrichment_hints=context.enrichment_hints,
    )
    target = InvestigationTarget(
        source="alert",
        scope="service",
        cluster="erauner-home",
        namespace="kagent",
        requested_target="service/controller",
        target="service/controller",
        node_name=None,
        service_name="controller",
        profile="service",
        lookback_minutes=15,
        normalization_notes=["alertname=EnvoyHighErrorRate"],
    )

    artifact_report = synthesize_root_cause(bundle, target)
    legacy_report = build_root_cause_report(
        context,
        CollectContextRequest(
            namespace="kagent",
            target="service/controller",
            profile="service",
            service_name="controller",
        ),
    )

    assert artifact_report == legacy_report
