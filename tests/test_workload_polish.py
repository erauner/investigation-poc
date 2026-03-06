from investigation_service.analysis import derive_findings
from investigation_service.models import CollectedContextResponse, Finding, NormalizedInvestigationRequest, TargetRef
from investigation_service.synthesis import build_root_cause_report


def test_workload_findings_do_not_include_otel_pipeline_noise() -> None:
    findings = derive_findings(
        "workload",
        {"kind": "pod", "name": "crashy"},
        ["BackOff restarting failed container"],
        "starting\nexit 1",
        {"accepted_spans_per_sec": 0, "pod_restart_rate": 0.0034},
    )

    titles = [item.title for item in findings]
    assert "Crash Loop Detected" in titles
    assert "Pod Restarts Increasing" in titles
    assert "No Active Span Ingestion" not in titles


def test_explicit_pod_request_uses_pod_specific_likely_cause() -> None:
    request = NormalizedInvestigationRequest(
        source="manual",
        scope="workload",
        namespace="kagent-smoke",
        target="pod/crashy",
        profile="workload",
        lookback_minutes=15,
        normalization_notes=[],
    )
    report = build_root_cause_report(
        CollectedContextResponse(
            target=TargetRef(namespace="kagent-smoke", kind="pod", name="crashy-abc123"),
            object_state={"kind": "pod", "name": "crashy-abc123"},
            events=["BackOff restarting failed container"],
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
            enrichment_hints=[],
        ),
        request,
    )

    assert report.likely_cause == "The pod is repeatedly failing shortly after start, so Kubernetes is backing off restarts."
