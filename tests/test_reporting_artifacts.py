from investigation_service.models import (
    BuildInvestigationPlanRequest,
    CollectedContextResponse,
    CorrelatedChangesResponse,
    EvidenceBundle,
    Finding,
    Hypothesis,
    InvestigationAnalysis,
    InvestigationPlan,
    InvestigationReportRequest,
    InvestigationTarget,
    PlanStep,
    RootCauseReport,
    TargetRef,
)
from investigation_service import reporting


def _target() -> InvestigationTarget:
    return InvestigationTarget(
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
    )


def _plan() -> InvestigationPlan:
    return InvestigationPlan(
        mode="targeted_rca",
        objective="Investigate service/api",
        target=_target(),
        steps=[
            PlanStep(
                id="collect-target-evidence",
                title="Collect service evidence",
                category="evidence",
                plane="service",
                rationale="Collect target evidence",
                suggested_tool="collect_service_evidence",
            )
        ],
        evidence_batches=[],
        planning_notes=["artifact-note"],
    )


def _context() -> CollectedContextResponse:
    return CollectedContextResponse(
        cluster="artifact-cluster",
        target=TargetRef(namespace="artifact-ns", kind="service", name="api-resolved"),
        object_state={"kind": "service", "name": "api-resolved"},
        events=[],
        log_excerpt="",
        metrics={},
        findings=[
            Finding(
                severity="warning",
                source="heuristic",
                title="Service instability",
                evidence="Observed instability in service signals",
            )
        ],
        limitations=[],
        enrichment_hints=[],
    )


def _bundle() -> EvidenceBundle:
    return EvidenceBundle(
        cluster="artifact-cluster",
        target=TargetRef(namespace="artifact-ns", kind="service", name="api-resolved"),
        object_state={"kind": "service", "name": "api-resolved"},
        events=[],
        log_excerpt="",
        metrics={},
        findings=[],
        limitations=[],
        enrichment_hints=[],
    )


def test_build_investigation_report_prefers_plan_target_fields(monkeypatch) -> None:
    correlation_request = {}

    monkeypatch.setattr(reporting, "build_investigation_plan", lambda req: _plan())
    monkeypatch.setattr(reporting, "_collect_context_for_normalized_request", lambda normalized: _context())
    monkeypatch.setattr(reporting, "evidence_bundle_from_context", lambda context: _bundle())
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

    assert report.normalization_notes == [
        "artifact-note",
        "cluster resolved from collected context: artifact-cluster",
    ]
    assert correlation_request["req"].cluster == "artifact-cluster"
    assert correlation_request["req"].namespace == "artifact-ns"
    assert correlation_request["req"].target == "service/api-resolved"


def test_build_investigation_report_uses_analysis_path_by_default(monkeypatch) -> None:
    monkeypatch.setattr(reporting, "build_investigation_plan", lambda req: _plan())
    monkeypatch.setattr(reporting, "_collect_context_for_normalized_request", lambda normalized: _context())
    monkeypatch.setattr(reporting, "evidence_bundle_from_context", lambda context: _bundle())
    monkeypatch.setattr(
        reporting,
        "_analyze_state",
        lambda _state: InvestigationAnalysis(
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
        lambda state: (_ for _ in ()).throw(AssertionError("root-cause path should not be used")),
    )
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    report = reporting.build_investigation_report(
        InvestigationReportRequest(target="service/api", profile="service", include_related_data=False)
    )

    assert report.diagnosis == "Artifact analysis"
    assert report.likely_cause == "Artifact likely cause"
    assert report.recommended_next_step == "artifact next step"


def test_build_investigation_report_softens_confidence_when_hypotheses_are_close(monkeypatch) -> None:
    monkeypatch.setattr(reporting, "build_investigation_plan", lambda req: _plan())
    monkeypatch.setattr(reporting, "_collect_context_for_normalized_request", lambda normalized: _context())
    monkeypatch.setattr(reporting, "evidence_bundle_from_context", lambda context: _bundle())
    monkeypatch.setattr(
        reporting,
        "_analyze_state",
        lambda _state: InvestigationAnalysis(
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
                    score=410,
                    supporting_findings=[],
                    evidence_items=[],
                ),
                Hypothesis(
                    key="latency",
                    diagnosis="Latency alternative",
                    likely_cause="Artifact secondary cause",
                    confidence="medium",
                    score=390,
                    supporting_findings=[],
                    evidence_items=[],
                ),
            ],
            limitations=[],
            recommended_next_step="artifact next step",
            suggested_follow_ups=["artifact follow-up"],
        ),
    )
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    report = reporting.build_investigation_report(
        InvestigationReportRequest(target="service/api", profile="service", include_related_data=False)
    )

    assert report.confidence == "medium"
    assert "multiple plausible causes remain" in report.limitations[0]
    assert any(
        "Validate the leading hypothesis against the next most plausible cause" in item
        for item in report.suggested_follow_ups
    )


def test_build_root_cause_report_is_alias_over_render(monkeypatch) -> None:
    monkeypatch.setattr(
        reporting,
        "render_investigation_report",
        lambda req: reporting.InvestigationReport(
            cluster="artifact-cluster",
            scope="service",
            target="service/api-resolved",
            diagnosis="Artifact analysis",
            likely_cause="Artifact likely cause",
            confidence="medium",
            evidence=["artifact evidence"],
            evidence_items=[],
            related_data=[],
            related_data_note=None,
            limitations=[],
            recommended_next_step="artifact next step",
            suggested_follow_ups=[],
            guidelines=[],
            normalization_notes=["artifact-note"],
        ),
    )

    report = reporting.build_root_cause_report(
        reporting.BuildRootCauseReportRequest(target="service/api", profile="service")
    )

    assert report.diagnosis == "Artifact analysis"
