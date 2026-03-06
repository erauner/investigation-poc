from investigation_service.models import CollectedContextResponse, EvidenceItem, InvestigationReportRequest, RootCauseReport, TargetRef
from investigation_service.reporting import build_investigation_report


def test_build_investigation_report_promotes_resolved_service_to_service_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_service.reporting._collect_context_for_normalized_request",
        lambda normalized: CollectedContextResponse(
            target=TargetRef(namespace="observability", kind="service", name="giraffe-kube-prometheus-st-prometheus"),
            object_state={"kind": "service", "name": "giraffe-kube-prometheus-st-prometheus"},
            events=["Warning PolicyViolation service label mismatch"],
            log_excerpt="",
            metrics={"profile": "service", "prometheus_available": False},
            findings=[],
            limitations=["metric unavailable: service_latency_p95_seconds"],
            enrichment_hints=[],
        ),
    )

    captured = {}

    def fake_build_root_cause(context, normalized):
        captured["normalized"] = normalized
        return RootCauseReport(
            scope=normalized.scope,
            target=f"{context.target.kind}/{context.target.name}",
            diagnosis="No Critical Signals Found",
            likely_cause=None,
            confidence="low",
            evidence=["heuristic: No obvious failure signature detected from current inputs"],
            evidence_items=[
                EvidenceItem(
                    fingerprint="finding|service|no critical signals found|no obvious failure signature detected from current inputs",
                    source="heuristic",
                    kind="finding",
                    severity="info",
                    summary="heuristic: No Critical Signals Found",
                    detail="No obvious failure signature detected from current inputs",
                )
            ],
            limitations=["metric unavailable: service_latency_p95_seconds"],
            recommended_next_step="Inspect service dashboards, recent deploys, and upstream or downstream dependencies before changing traffic handling.",
            suggested_follow_ups=[],
        )

    monkeypatch.setattr("investigation_service.reporting.build_root_cause_report_impl", fake_build_root_cause)
    monkeypatch.setattr(
        "investigation_service.reporting.load_guideline_rules",
        lambda: ([], []),
    )

    report = build_investigation_report(
        InvestigationReportRequest(
            namespace="observability",
            target="giraffe-kube-prometheus-st-prometheus",
            profile="workload",
            include_related_data=False,
        )
    )

    normalized = captured["normalized"]
    assert normalized.scope == "service"
    assert normalized.profile == "service"
    assert normalized.target == "service/giraffe-kube-prometheus-st-prometheus"
    assert normalized.service_name == "giraffe-kube-prometheus-st-prometheus"
    assert "profile promoted to service after resolving target kind=service" in normalized.normalization_notes
    assert report.recommended_next_step.startswith("Inspect service dashboards")
