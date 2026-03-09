from investigation_service.models import (
    CollectedContextResponse,
    EvidenceBatchExecution,
    Hypothesis,
    InvestigationAnalysis,
    InvestigationReportRequest,
    TargetRef,
)
from investigation_service import reporting


def _patch_execution(monkeypatch, context: CollectedContextResponse) -> None:
    monkeypatch.setattr(
        reporting,
        "execute_investigation_step",
        lambda _req: EvidenceBatchExecution(
            batch_id="batch-1",
            executed_step_ids=["collect-target-evidence"],
            artifacts=[
                {
                    "step_id": "collect-target-evidence",
                    "plane": "service" if context.target.kind == "service" else "workload",
                    "artifact_type": "evidence_bundle",
                    "summary": [],
                    "limitations": list(context.limitations),
                    "evidence_bundle": {
                        "cluster": context.cluster,
                        "target": context.target.model_dump(mode="json"),
                        "object_state": context.object_state,
                        "events": context.events,
                        "log_excerpt": context.log_excerpt,
                        "metrics": context.metrics,
                        "findings": [item.model_dump(mode="json") for item in context.findings],
                        "limitations": context.limitations,
                        "enrichment_hints": context.enrichment_hints,
                    },
                }
            ],
            execution_notes=[],
        ),
    )
    monkeypatch.setattr(
        reporting,
        "update_investigation_plan",
        lambda req: req.plan.model_copy(update={"active_batch_id": None}),
    )
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))


def _capture_state_analysis(captured: dict, *, diagnosis: str, target: str, scope: str) -> InvestigationAnalysis:
    return InvestigationAnalysis(
        cluster="erauner-home",
        scope=scope,
        target=target,
        profile=scope if scope in {"service", "workload"} else "workload",
        hypotheses=[
            Hypothesis(
                key="captured",
                diagnosis=diagnosis,
                likely_cause=None,
                confidence="medium" if scope == "service" else "high",
                score=1,
                supporting_findings=[],
                evidence_items=[],
            )
        ],
        limitations=[],
        recommended_next_step="Inspect the current evidence before taking write actions.",
        suggested_follow_ups=[],
    )


def _capture_state(captured: dict, *, diagnosis: str, target: str, scope: str):
    def fake_rank(state):
        captured["state"] = state
        return _capture_state_analysis(captured, diagnosis=diagnosis, target=target, scope=scope)

    return fake_rank


def test_build_investigation_report_rewrites_explicit_pod_prefix_to_resolved_pod(monkeypatch) -> None:
    captured = {}
    _patch_execution(
        monkeypatch,
        CollectedContextResponse(
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
    monkeypatch.setattr(
        reporting,
        "rank_hypotheses_from_state",
        _capture_state(captured, diagnosis="Crash Loop Detected", target="pod/crashy-6f5689f4cd-czdlg", scope="workload"),
    )

    reporting.build_investigation_report(
        InvestigationReportRequest(namespace="kagent-smoke", target="pod/crashy", include_related_data=False)
    )

    assert captured["state"].target.target == "pod/crashy-6f5689f4cd-czdlg"


def test_build_investigation_report_resolves_backend_target_to_deployment(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        reporting,
        "resolve_cluster",
        lambda cluster: type("ResolvedCluster", (), {"alias": cluster or "erauner-home"})(),
    )
    monkeypatch.setattr(reporting, "get_backend_cr", lambda namespace, name, cluster=None: {"metadata": {"name": name}})
    _patch_execution(
        monkeypatch,
        CollectedContextResponse(
            cluster="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="pod", name="crashy"),
            object_state={"kind": "deployment", "name": "crashy"},
            events=["BackOff restarting failed container"],
            log_excerpt="starting",
            metrics={"pod_restart_rate": 0.0034},
            findings=[],
            limitations=[],
            enrichment_hints=[],
        ),
    )
    monkeypatch.setattr(
        reporting,
        "rank_hypotheses_from_state",
        _capture_state(captured, diagnosis="Crash Loop Detected", target="deployment/crashy", scope="workload"),
    )

    report = reporting.build_investigation_report(
        InvestigationReportRequest(namespace="operator-smoke", target="Backend/crashy", include_related_data=False)
    )

    target = captured["state"].target
    assert target.scope == "workload"
    assert target.profile == "workload"
    assert target.target == "deployment/crashy"
    assert target.service_name == "crashy"
    assert "resolved Backend/crashy to deployment/crashy" in target.normalization_notes
    assert report.target == "deployment/crashy"


def test_build_investigation_report_resolves_frontend_target_to_deployment(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        reporting,
        "resolve_cluster",
        lambda cluster: type("ResolvedCluster", (), {"alias": cluster or "erauner-home"})(),
    )
    monkeypatch.setattr(reporting, "get_frontend_cr", lambda namespace, name, cluster=None: {"metadata": {"name": name}})
    _patch_execution(
        monkeypatch,
        CollectedContextResponse(
            cluster="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="pod", name="landing"),
            object_state={"kind": "deployment", "name": "landing"},
            events=["Deployment rollout progressing"],
            log_excerpt="starting",
            metrics={"pod_restart_rate": 0.0},
            findings=[],
            limitations=[],
            enrichment_hints=[],
        ),
    )
    monkeypatch.setattr(
        reporting,
        "rank_hypotheses_from_state",
        _capture_state(captured, diagnosis="No Critical Signals Found", target="deployment/landing", scope="workload"),
    )

    report = reporting.build_investigation_report(
        InvestigationReportRequest(namespace="operator-smoke", target="Frontend/landing", include_related_data=False)
    )

    target = captured["state"].target
    assert target.scope == "workload"
    assert target.profile == "workload"
    assert target.target == "deployment/landing"
    assert target.service_name == "landing"
    assert "resolved Frontend/landing to deployment/landing" in target.normalization_notes
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
    _patch_execution(
        monkeypatch,
        CollectedContextResponse(
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
    monkeypatch.setattr(
        reporting,
        "rank_hypotheses_from_state",
        _capture_state(captured, diagnosis="Elevated Error Rate", target="service/landing", scope="service"),
    )

    report = reporting.build_investigation_report(
        InvestigationReportRequest(
            namespace="operator-smoke",
            target="Frontend/landing",
            profile="service",
            include_related_data=False,
        )
    )

    target = captured["state"].target
    assert target.scope == "service"
    assert target.profile == "service"
    assert target.service_name == "landing"
    assert target.target == "service/landing"
    assert "resolved Frontend/landing to service/landing" in target.normalization_notes
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
    _patch_execution(
        monkeypatch,
        CollectedContextResponse(
            cluster="erauner-home",
            target=TargetRef(namespace="operator-smoke", kind="pod", name="api"),
            object_state={"kind": "deployment", "name": "api"},
            events=["BackOff restarting failed container"],
            log_excerpt="starting",
            metrics={"pod_restart_rate": 0.0034},
            findings=[],
            limitations=[],
            enrichment_hints=[],
        ),
    )
    monkeypatch.setattr(
        reporting,
        "rank_hypotheses_from_state",
        _capture_state(captured, diagnosis="Crash Loop Detected", target="deployment/api", scope="workload"),
    )

    report = reporting.build_investigation_report(
        InvestigationReportRequest(namespace="operator-smoke", target="Cluster/testapp", include_related_data=False)
    )

    target = captured["state"].target
    assert target.scope == "workload"
    assert target.profile == "workload"
    assert target.target == "deployment/api"
    assert target.service_name == "api"
    assert "resolved Cluster/testapp to failing component Backend/api" in target.normalization_notes
    assert "resolved Backend/api to deployment/api" in target.normalization_notes
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
    _patch_execution(
        monkeypatch,
        CollectedContextResponse(
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
    monkeypatch.setattr(
        reporting,
        "rank_hypotheses_from_state",
        _capture_state(captured, diagnosis="Elevated Error Rate", target="service/landing", scope="service"),
    )

    report = reporting.build_investigation_report(
        InvestigationReportRequest(
            namespace="operator-smoke",
            target="Cluster/testapp",
            profile="service",
            include_related_data=False,
        )
    )

    target = captured["state"].target
    assert target.scope == "service"
    assert target.profile == "service"
    assert target.service_name == "landing"
    assert target.target == "service/landing"
    assert "resolved Cluster/testapp to failing component Frontend/landing" in target.normalization_notes
    assert "resolved Frontend/landing to service/landing" in target.normalization_notes
    assert report.target == "service/landing"


def test_build_investigation_report_rewrites_alert_shaped_pod_to_resolved_pod(monkeypatch) -> None:
    captured = {}
    _patch_execution(
        monkeypatch,
        CollectedContextResponse(
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

    monkeypatch.setattr(reporting, "normalize_alert_input", fake_normalize_alert)
    monkeypatch.setattr(
        reporting,
        "rank_hypotheses_from_state",
        _capture_state(captured, diagnosis="Crash Loop Detected", target="pod/crashy-6f5689f4cd-czdlg", scope="workload"),
    )

    reporting.build_investigation_report(
        InvestigationReportRequest(
            alertname="PodCrashLooping",
            namespace="kagent-smoke",
            target="pod/crashy",
            include_related_data=False,
        )
    )

    assert captured["state"].target.target == "pod/crashy-6f5689f4cd-czdlg"
