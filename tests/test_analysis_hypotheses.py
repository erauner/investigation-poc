from investigation_service.analysis import build_investigation_analysis
from investigation_service.models import EvidenceBundle, Finding, InvestigationTarget, TargetRef


def test_build_investigation_analysis_ranks_crash_loop_hypothesis_first() -> None:
    bundle = EvidenceBundle(
        cluster="erauner-home",
        target=TargetRef(namespace="default", kind="pod", name="crashy-123"),
        object_state={"kind": "pod", "name": "crashy-123"},
        events=["Warning BackOff pod/crashy-123 Back-off restarting failed container"],
        log_excerpt="starting",
        metrics={"pod_restart_rate": 0.0034},
        findings=[
            Finding(
                severity="warning",
                source="prometheus",
                title="Pod Restarts Increasing",
                evidence="Restart rate over lookback window: 0.0034/s",
            ),
            Finding(
                severity="critical",
                source="events",
                title="Crash Loop Detected",
                evidence="Events indicate BackOff/CrashLoopBackOff behavior",
            ),
        ],
        limitations=[],
        enrichment_hints=[],
    )
    target = InvestigationTarget(
        source="manual",
        scope="workload",
        cluster="erauner-home",
        namespace="default",
        requested_target="pod/crashy-123",
        target="pod/crashy-123",
        node_name=None,
        service_name=None,
        profile="workload",
        lookback_minutes=15,
        normalization_notes=[],
    )

    analysis = build_investigation_analysis(bundle, target)

    assert analysis.hypotheses[0].diagnosis == "Crash Loop Detected"
    assert len(analysis.hypotheses) <= 3


def test_build_investigation_analysis_keeps_service_signals_bounded() -> None:
    bundle = EvidenceBundle(
        cluster="erauner-home",
        target=TargetRef(namespace="kagent", kind="service", name="controller"),
        object_state={"kind": "service", "name": "controller"},
        events=["no related events"],
        log_excerpt="",
        metrics={"service_error_rate": 0.12, "service_latency_p95_seconds": 1.8},
        findings=[
            Finding(
                severity="warning",
                source="prometheus",
                title="High Service Latency",
                evidence="p95 latency is 1.800s",
            ),
            Finding(
                severity="warning",
                source="prometheus",
                title="Service Returning 5xx Responses",
                evidence="5xx request rate over lookback window: 0.1200/s",
            ),
        ],
        limitations=[],
        enrichment_hints=[],
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

    analysis = build_investigation_analysis(bundle, target)

    assert analysis.hypotheses[0].diagnosis == "Service Returning 5xx Responses"
    assert len(analysis.hypotheses) == 2
