from investigation_service.models import (
    CorrelatedChangesResponse,
    EvidenceBundle,
    Hypothesis,
    InvestigationAnalysis,
    InvestigationReportRequest,
    InvestigationTarget,
    NormalizedInvestigationRequest,
    PlannedInvestigation,
    RootCauseReport,
    TargetRef,
)
from investigation_service import reporting


def test_build_investigation_report_prefers_artifact_fields(monkeypatch) -> None:
    plan = PlannedInvestigation(
        mode="generic",
        target=InvestigationTarget(
            source="manual",
            scope="service",
            cluster="artifact-cluster",
            namespace="artifact-ns",
            requested_target="service/api",
            target="service/api-resolved",
            node_name=None,
            service_name="api-resolved",
            profile="service",
            lookback_minutes=15,
            normalization_notes=["artifact-note"],
        ),
        evidence=EvidenceBundle(
            cluster="artifact-cluster",
            target=TargetRef(namespace="artifact-ns", kind="service", name="api-resolved"),
            object_state={"kind": "service", "name": "api-resolved"},
            events=[],
            log_excerpt="",
            metrics={},
            findings=[],
            limitations=[],
            enrichment_hints=[],
        ),
        normalized=NormalizedInvestigationRequest(
            source="manual",
            scope="service",
            cluster="legacy-cluster",
            namespace="legacy-ns",
            target="service/api-legacy",
            node_name=None,
            service_name="api-legacy",
            profile="service",
            lookback_minutes=15,
            normalization_notes=["legacy-note"],
        ),
        context=type("LegacyContext", (), {"cluster": "legacy-cluster"})(),
    )
    correlation_request = {}

    monkeypatch.setattr(reporting.planner, "plan_investigation", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        reporting,
        "synthesize_root_cause_impl",
        lambda bundle, target: RootCauseReport(
            cluster=target.cluster or "artifact-cluster",
            scope=target.scope,
            target=target.target,
            diagnosis="Artifact path",
            likely_cause=None,
            confidence="medium",
            evidence=["artifact evidence"],
            evidence_items=[],
            limitations=[],
            recommended_next_step="artifact next step",
            suggested_follow_ups=[],
        ),
    )
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))
    monkeypatch.setattr(
        reporting,
        "collect_correlated_changes",
        lambda req: (
            correlation_request.setdefault("req", req),
            CorrelatedChangesResponse(
                cluster=req.cluster or "artifact-cluster",
                scope=req.profile,
                target=req.target,
                changes=[],
                limitations=[],
            ),
        )[1],
    )

    report = reporting.build_investigation_report(
        InvestigationReportRequest(target="service/api", profile="service", include_related_data=True)
    )

    assert report.normalization_notes == ["artifact-note"]
    assert correlation_request["req"].cluster == "artifact-cluster"
    assert correlation_request["req"].namespace == "artifact-ns"
    assert correlation_request["req"].target == "service/api-resolved"


def test_build_investigation_report_uses_analysis_path_by_default(monkeypatch) -> None:
    plan = PlannedInvestigation(
        mode="generic",
        target=InvestigationTarget(
            source="manual",
            scope="service",
            cluster="artifact-cluster",
            namespace="artifact-ns",
            requested_target="service/api",
            target="service/api-resolved",
            node_name=None,
            service_name="api-resolved",
            profile="service",
            lookback_minutes=15,
            normalization_notes=["artifact-note"],
        ),
        evidence=EvidenceBundle(
            cluster="artifact-cluster",
            target=TargetRef(namespace="artifact-ns", kind="service", name="api-resolved"),
            object_state={"kind": "service", "name": "api-resolved"},
            events=[],
            log_excerpt="",
            metrics={},
            findings=[],
            limitations=[],
            enrichment_hints=[],
        ),
        normalized=NormalizedInvestigationRequest(
            source="manual",
            scope="service",
            cluster="legacy-cluster",
            namespace="legacy-ns",
            target="service/api-legacy",
            node_name=None,
            service_name="api-legacy",
            profile="service",
            lookback_minutes=15,
            normalization_notes=["legacy-note"],
        ),
        context=type("LegacyContext", (), {"cluster": "legacy-cluster"})(),
    )

    monkeypatch.setattr(reporting.planner, "plan_investigation", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        reporting,
        "_analyze_plan",
        lambda _plan: InvestigationAnalysis(
            cluster="artifact-cluster",
            scope="service",
            target="service/api-resolved",
            profile="service",
            hypotheses=[
                Hypothesis(
                    key="service-5xx",
                    diagnosis="Artifact analysis",
                    likely_cause="Artifact likely cause",
                    confidence="high",
                    score=1,
                    supporting_findings=[],
                    evidence_items=[],
                )
            ],
            limitations=[],
            recommended_next_step="artifact next step",
            suggested_follow_ups=["artifact follow-up"],
        ),
    )
    monkeypatch.setattr(
        reporting,
        "_synthesize_root_cause",
        lambda plan: (_ for _ in ()).throw(AssertionError("root-cause path should not be used")),
    )
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    report = reporting.build_investigation_report(
        InvestigationReportRequest(target="service/api", profile="service", include_related_data=False)
    )

    assert report.diagnosis == "Artifact analysis"
    assert report.likely_cause == "Artifact likely cause"
    assert report.recommended_next_step == "artifact next step"
