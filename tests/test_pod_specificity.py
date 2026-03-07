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


def test_build_investigation_report_resolves_backend_target_to_deployment(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(
        reporting,
        "resolve_cluster",
        lambda cluster: type("ResolvedCluster", (), {"alias": cluster or "erauner-home"})(),
    )
    monkeypatch.setattr(reporting, "get_backend_cr", lambda namespace, name, cluster=None: {"metadata": {"name": name}})
    monkeypatch.setattr(
        reporting,
        "_collect_context_for_normalized_request",
        lambda normalized: CollectedContextResponse(
            cluster="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
            object_state={"kind": "deployment", "name": "crashy"},
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
            target="deployment/crashy",
            diagnosis="Crash Loop Detected",
            likely_cause="The pod is repeatedly failing shortly after start, so Kubernetes is backing off restarts.",
            confidence="high",
            evidence=["events: Crash Loop Detected - Events indicate BackOff/CrashLoopBackOff behavior"],
            evidence_items=[],
            limitations=[],
            recommended_next_step="Confirm the failure with describe output, recent logs, and rollout history before taking write actions.",
            suggested_follow_ups=[],
        )

    monkeypatch.setattr(reporting, "build_root_cause_report_impl", fake_build_root_cause)
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    report = reporting.build_investigation_report(
        InvestigationReportRequest(namespace="operator-smoke", target="Backend/crashy", include_related_data=False)
    )

    normalized = captured["normalized"]
    assert normalized.scope == "workload"
    assert normalized.profile == "workload"
    assert normalized.target == "deployment/crashy"
    assert "resolved Backend/crashy to deployment/crashy" in normalized.normalization_notes
    assert report.target == "deployment/crashy"


def test_build_investigation_report_resolves_frontend_target_to_deployment(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(
        reporting,
        "resolve_cluster",
        lambda cluster: type("ResolvedCluster", (), {"alias": cluster or "erauner-home"})(),
    )
    monkeypatch.setattr(reporting, "get_frontend_cr", lambda namespace, name, cluster=None: {"metadata": {"name": name}})
    monkeypatch.setattr(
        reporting,
        "_collect_context_for_normalized_request",
        lambda normalized: CollectedContextResponse(
            cluster="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="deployment", name="landing"),
            object_state={"kind": "deployment", "name": "landing"},
            events=["Deployment rollout progressing"],
            log_excerpt="starting",
            metrics={"pod_restart_rate": 0.0},
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
            target="deployment/landing",
            diagnosis="No Critical Signals Found",
            likely_cause=None,
            confidence="low",
            evidence=["heuristic: No obvious failure signature detected from current inputs"],
            evidence_items=[],
            limitations=[],
            recommended_next_step="Confirm the failure with describe output, recent logs, and rollout history before taking write actions.",
            suggested_follow_ups=[],
        )

    monkeypatch.setattr(reporting, "build_root_cause_report_impl", fake_build_root_cause)
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    report = reporting.build_investigation_report(
        InvestigationReportRequest(namespace="operator-smoke", target="Frontend/landing", include_related_data=False)
    )

    normalized = captured["normalized"]
    assert normalized.scope == "workload"
    assert normalized.profile == "workload"
    assert normalized.target == "deployment/landing"
    assert "resolved Frontend/landing to deployment/landing" in normalized.normalization_notes
    assert report.target == "deployment/landing"


def test_build_investigation_report_resolves_frontend_service_profile_to_service(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(
        reporting,
        "resolve_cluster",
        lambda cluster: type("ResolvedCluster", (), {"alias": cluster or "erauner-home"})(),
    )
    monkeypatch.setattr(
        reporting,
        "get_frontend_cr",
        lambda namespace, name, cluster=None: {"kind": "Frontend", "metadata": {"name": name, "namespace": namespace}},
    )
    monkeypatch.setattr(
        reporting,
        "_collect_context_for_normalized_request",
        lambda normalized: CollectedContextResponse(
            cluster="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="service", name="landing"),
            object_state={"kind": "service", "name": "landing"},
            events=[],
            log_excerpt="",
            metrics={"http_error_ratio": 0.12},
            findings=[],
            limitations=[],
            enrichment_hints=[],
        ),
    )

    def fake_build_root_cause(context, normalized):
        captured["normalized"] = normalized
        return RootCauseReport(
            cluster="erauner-home",
            scope="service",
            target="service/landing",
            diagnosis="Elevated Error Rate",
            likely_cause=None,
            confidence="medium",
            evidence=["metrics: HTTP error ratio is elevated"],
            evidence_items=[],
            limitations=[],
            recommended_next_step="Inspect service-level error spikes and correlate them with the backing deployment rollout.",
            suggested_follow_ups=[],
        )

    monkeypatch.setattr(reporting, "build_root_cause_report_impl", fake_build_root_cause)
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    report = reporting.build_investigation_report(
        InvestigationReportRequest(
            namespace="operator-smoke",
            target="Frontend/landing",
            profile="service",
            include_related_data=False,
        )
    )

    normalized = captured["normalized"]
    assert normalized.scope == "service"
    assert normalized.profile == "service"
    assert normalized.service_name == "landing"
    assert normalized.target == "service/landing"
    assert "resolved Frontend/landing to service/landing" in normalized.normalization_notes
    assert report.target == "service/landing"


def test_build_investigation_report_resolves_cluster_target_to_failing_component(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(
        reporting,
        "resolve_cluster",
        lambda cluster: type("ResolvedCluster", (), {"alias": cluster or "erauner-home"})(),
    )
    monkeypatch.setattr(
        reporting,
        "get_cluster_cr",
        lambda namespace, name, cluster=None: {
            "status": {
                "componentStatuses": [
                    {"name": "landing", "kind": "Frontend", "wave": 2, "phase": "Healthy", "ready": True},
                    {"name": "api", "kind": "Backend", "wave": 1, "phase": "Failed", "ready": False},
                ]
            }
        },
    )
    monkeypatch.setattr(
        reporting,
        "_collect_context_for_normalized_request",
        lambda normalized: CollectedContextResponse(
            cluster="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="deployment", name="api"),
            object_state={"kind": "deployment", "name": "api"},
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
            target="deployment/api",
            diagnosis="Crash Loop Detected",
            likely_cause="The pod is repeatedly failing shortly after start, so Kubernetes is backing off restarts.",
            confidence="high",
            evidence=["events: Crash Loop Detected - Events indicate BackOff/CrashLoopBackOff behavior"],
            evidence_items=[],
            limitations=[],
            recommended_next_step="Confirm the failure with describe output, recent logs, and rollout history before taking write actions.",
            suggested_follow_ups=[],
        )

    monkeypatch.setattr(reporting, "build_root_cause_report_impl", fake_build_root_cause)
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    report = reporting.build_investigation_report(
        InvestigationReportRequest(namespace="operator-smoke", target="Cluster/testapp", include_related_data=False)
    )

    normalized = captured["normalized"]
    assert normalized.scope == "workload"
    assert normalized.profile == "workload"
    assert normalized.target == "deployment/api"
    assert "resolved Cluster/testapp to failing component Backend/api" in normalized.normalization_notes
    assert "resolved Backend/api to deployment/api" in normalized.normalization_notes
    assert report.target == "deployment/api"


def test_build_investigation_report_resolves_cluster_service_profile_to_frontend_service(monkeypatch) -> None:
    captured = {}

    monkeypatch.setattr(
        reporting,
        "resolve_cluster",
        lambda cluster: type("ResolvedCluster", (), {"alias": cluster or "erauner-home"})(),
    )
    monkeypatch.setattr(
        reporting,
        "get_cluster_cr",
        lambda namespace, name, cluster=None: {
            "status": {
                "componentStatuses": [
                    {"name": "landing", "kind": "Frontend", "wave": 2, "phase": "Failed", "ready": False},
                    {"name": "api", "kind": "Backend", "wave": 1, "phase": "Healthy", "ready": True},
                ]
            }
        },
    )
    monkeypatch.setattr(
        reporting,
        "_collect_context_for_normalized_request",
        lambda normalized: CollectedContextResponse(
            cluster="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="service", name="landing"),
            object_state={"kind": "service", "name": "landing"},
            events=[],
            log_excerpt="",
            metrics={"http_error_ratio": 0.12},
            findings=[],
            limitations=[],
            enrichment_hints=[],
        ),
    )

    def fake_build_root_cause(context, normalized):
        captured["normalized"] = normalized
        return RootCauseReport(
            cluster="erauner-home",
            scope="service",
            target="service/landing",
            diagnosis="Elevated Error Rate",
            likely_cause=None,
            confidence="medium",
            evidence=["metrics: HTTP error ratio is elevated"],
            evidence_items=[],
            limitations=[],
            recommended_next_step="Inspect service-level error spikes and correlate them with the backing deployment rollout.",
            suggested_follow_ups=[],
        )

    monkeypatch.setattr(reporting, "build_root_cause_report_impl", fake_build_root_cause)
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    report = reporting.build_investigation_report(
        InvestigationReportRequest(
            namespace="operator-smoke",
            target="Cluster/testapp",
            profile="service",
            include_related_data=False,
        )
    )

    normalized = captured["normalized"]
    assert normalized.scope == "service"
    assert normalized.profile == "service"
    assert normalized.service_name == "landing"
    assert normalized.target == "service/landing"
    assert "resolved Cluster/testapp to failing component Frontend/landing" in normalized.normalization_notes
    assert "resolved Frontend/landing to service/landing" in normalized.normalization_notes
    assert report.target == "service/landing"


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
