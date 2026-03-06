from investigation_service.models import CollectedContextResponse, Finding, NormalizedInvestigationRequest, TargetRef
from investigation_service.reporting import _filter_related_data
from investigation_service.synthesis import build_root_cause_report


def test_empty_related_data_note_uses_canonical_wording() -> None:
    request = NormalizedInvestigationRequest(
        source="manual",
        scope="service",
        namespace="observability",
        target="service/api",
        service_name="api",
        profile="service",
        lookback_minutes=15,
        normalization_notes=[],
    )
    report = build_root_cause_report(
        CollectedContextResponse(
            target=TargetRef(namespace="observability", kind="service", name="api"),
            object_state={"kind": "service", "name": "api"},
            events=["no related events"],
            log_excerpt="",
            metrics={"profile": "service", "prometheus_available": False},
            findings=[
                Finding(
                    severity="info",
                    source="heuristic",
                    title="No Critical Signals Found",
                    evidence="No obvious failure signature detected from current inputs",
                )
            ],
            limitations=["metric unavailable: service_latency_p95_seconds"],
            enrichment_hints=[],
        ),
        request,
    )

    related_data, note = _filter_related_data(report, [])

    assert related_data == []
    assert note == "No meaningful related data found in the requested time window."


def test_low_signal_service_diagnosis_is_inconclusive() -> None:
    request = NormalizedInvestigationRequest(
        source="manual",
        scope="service",
        namespace="observability",
        target="service/api",
        service_name="api",
        profile="service",
        lookback_minutes=15,
        normalization_notes=[],
    )
    report = build_root_cause_report(
        CollectedContextResponse(
            target=TargetRef(namespace="observability", kind="service", name="api"),
            object_state={"kind": "service", "name": "api"},
            events=["Warning PolicyViolation service label mismatch"],
            log_excerpt="",
            metrics={"profile": "service", "prometheus_available": False},
            findings=[
                Finding(
                    severity="info",
                    source="heuristic",
                    title="No Critical Signals Found",
                    evidence="No obvious failure signature detected from current inputs",
                )
            ],
            limitations=[
                "metric unavailable: service_request_rate",
                "metric unavailable: service_latency_p95_seconds",
            ],
            enrichment_hints=[],
        ),
        request,
    )

    assert report.diagnosis == "Service Signals Inconclusive"
