from investigation_service.models import CollectedContextResponse, EvidenceItem, InvestigationReportRequest, RootCauseReport, TargetRef
from investigation_service import reporting


def test_build_investigation_report_rewrites_explicit_pod_prefix_to_resolved_pod(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(
        reporting,
        "_collect_context_for_normalized_request",
        lambda normalized: CollectedContextResponse(
            cluster="erauner-home",
            target=TargetRef(namespace="kagent-smoke", kind="pod", name="crashy-6f5689f4cd-czdlg"),
            object_state={"kind": "pod", "name": "crashy-6f5689f4cd-czdlg"},
            events=["BackOff restarting failed container"],
            log_excerpt="starting",
            metrics={"pod_restart_rate": 0.0034},
            findings=[],
            limitations=[],
            enrichment_hints=[],
        ),
    )

    def fake_build_root_cause(context, normalized):
        captured["normalized"] = normalized
        return RootCauseReport(
            cluster="erauner-home",
            scope="workload",
            target="pod/crashy-6f5689f4cd-czdlg",
            diagnosis="Crash Loop Detected",
            likely_cause="The pod is repeatedly failing shortly after start, so Kubernetes is backing off restarts.",
            confidence="high",
            evidence=["events: Crash Loop Detected - Events indicate BackOff/CrashLoopBackOff behavior"],
            evidence_items=[
                EvidenceItem(
                    fingerprint="finding|workload|crash loop detected|events indicate backoff/crashloopbackoff behavior",
                    source="events",
                    kind="finding",
                    severity="critical",
                    summary="events: Crash Loop Detected",
                    detail="Events indicate BackOff/CrashLoopBackOff behavior",
                )
            ],
            limitations=[],
            recommended_next_step="Confirm the failure with describe output, recent logs, and rollout history before taking write actions.",
            suggested_follow_ups=[],
        )

    monkeypatch.setattr(reporting, "build_root_cause_report_impl", fake_build_root_cause)
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    report = reporting.build_investigation_report(
        InvestigationReportRequest(namespace="kagent-smoke", target="pod/crashy", include_related_data=False)
    )

    normalized = captured["normalized"]
    assert normalized.target == "pod/crashy-6f5689f4cd-czdlg"
    assert "resolved pod target to crashy-6f5689f4cd-czdlg" in normalized.normalization_notes
    assert report.target == "pod/crashy-6f5689f4cd-czdlg"


def test_build_investigation_report_rewrites_alert_shaped_pod_to_resolved_pod(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(
        reporting,
        "_collect_context_for_normalized_request",
        lambda normalized: CollectedContextResponse(
            cluster="erauner-home",
            target=TargetRef(namespace="kagent-smoke", kind="pod", name="crashy-6f5689f4cd-czdlg"),
            object_state={"kind": "pod", "name": "crashy-6f5689f4cd-czdlg"},
            events=["BackOff restarting failed container"],
            log_excerpt="starting",
            metrics={"pod_restart_rate": 0.0034},
            findings=[],
            limitations=[],
            enrichment_hints=[],
        ),
    )

    def fake_normalize_alert(req):
        from investigation_service.models import NormalizedInvestigationRequest

        return NormalizedInvestigationRequest(
            source="alert",
            scope="workload",
            cluster="erauner-home",
            namespace="kagent-smoke",
            target="pod/crashy",
            profile="workload",
            lookback_minutes=15,
            normalization_notes=["alertname=PodCrashLooping"],
        )

    def fake_build_root_cause(context, normalized):
        captured["normalized"] = normalized
        return RootCauseReport(
            cluster="erauner-home",
            scope="workload",
            target="pod/crashy-6f5689f4cd-czdlg",
            diagnosis="Crash Loop Detected",
            likely_cause="The pod is repeatedly failing shortly after start, so Kubernetes is backing off restarts.",
            confidence="high",
            evidence=["events: Crash Loop Detected - Events indicate BackOff/CrashLoopBackOff behavior"],
            evidence_items=[],
            limitations=[],
            recommended_next_step="Confirm the failure with describe output, recent logs, and rollout history before taking write actions.",
            suggested_follow_ups=[],
        )

    monkeypatch.setattr(reporting, "normalize_alert_input", fake_normalize_alert)
    monkeypatch.setattr(reporting, "build_root_cause_report_impl", fake_build_root_cause)
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    reporting.build_investigation_report(
        InvestigationReportRequest(
            alertname="PodCrashLooping",
            namespace="kagent-smoke",
            target="pod/crashy",
            include_related_data=False,
        )
    )

    normalized = captured["normalized"]
    assert normalized.target == "pod/crashy-6f5689f4cd-czdlg"
