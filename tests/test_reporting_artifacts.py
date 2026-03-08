from investigation_service.models import (
    CorrelatedChangesResponse,
    EvidenceBundle,
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
