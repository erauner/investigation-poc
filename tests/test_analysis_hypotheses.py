from investigation_service.analysis import (
    adjusted_confidence_from_hypotheses,
    ambiguity_limitations_from_hypotheses,
    build_primary_evidence_from_bundle,
    build_investigation_analysis,
    follow_ups_from_hypotheses,
)
from investigation_service.models import EvidenceBundle, Finding, Hypothesis, InvestigationAnalysis, InvestigationTarget, TargetRef


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


def test_close_secondary_hypotheses_reduce_confidence_and_add_follow_up() -> None:
    analysis = InvestigationAnalysis(
        cluster="erauner-home",
        scope="service",
        target="service/api",
        profile="service",
        hypotheses=[
            Hypothesis(
                key="service-5xx",
                diagnosis="Service Returning 5xx Responses",
                likely_cause="backend failure",
                confidence="high",
                score=420,
                supporting_findings=[],
                evidence_items=[],
            ),
            Hypothesis(
                key="latency",
                diagnosis="High Service Latency",
                likely_cause="dependency slowness",
                confidence="medium",
                score=395,
                supporting_findings=[],
                evidence_items=[],
            ),
        ],
        limitations=[],
        recommended_next_step="inspect metrics",
        suggested_follow_ups=[],
    )

    assert adjusted_confidence_from_hypotheses(analysis) == "medium"
    assert ambiguity_limitations_from_hypotheses(analysis) == [
        "multiple plausible causes remain; alternative hypotheses include High Service Latency"
    ]
    assert follow_ups_from_hypotheses(analysis) == [
        "Validate the leading hypothesis against the next most plausible cause before taking write actions."
    ]


def test_build_primary_evidence_from_bundle_surfaces_node_top_pod_summary() -> None:
    bundle = EvidenceBundle(
        cluster="erauner-home",
        target=TargetRef(namespace=None, kind="node", name="worker3"),
        object_state={
            "kind": "node",
            "name": "worker3",
            "conditions": [],
            "top_pods_by_memory_request": [
                {"namespace": "operator-smoke", "name": "api-0", "memory_request_bytes": 536870912}
            ],
        },
        events=["Warning DiskPressure node/worker3"],
        log_excerpt="",
        metrics={
            "node_memory_allocatable_bytes": 100.0,
            "node_memory_working_set_bytes": 40.0,
            "node_memory_request_bytes": 90.0,
        },
        findings=[
            Finding(
                severity="warning",
                source="prometheus",
                title="High Node Memory Request Saturation",
                evidence="Memory requests are at 90.0% of allocatable capacity",
            )
        ],
        limitations=[],
        enrichment_hints=[],
    )

    evidence_items = build_primary_evidence_from_bundle(bundle, "node")

    assert any(item.summary == "k8s: Top Node Memory Request Consumers" for item in evidence_items)
