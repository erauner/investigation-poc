from investigation_service.correlation import _change_from_event
from investigation_service.event_fingerprints import canonicalize_event_fingerprint
from investigation_service.models import CollectedContextResponse, CorrelatedChange, EvidenceItem, RootCauseReport, TargetRef
from investigation_service.reporting import _filter_related_data
from investigation_service.synthesis import build_primary_evidence


def test_service_event_fingerprint_matches_correlated_change() -> None:
    context = CollectedContextResponse(
        target=TargetRef(namespace="observability", kind="service", name="giraffe-kube-prometheus-st-prometheus"),
        object_state={},
        events=[
            "Warning PolicyViolation policy require-consistent-service-deployment-labels fail: Services must have homelab.dev/layer"
        ],
        log_excerpt="",
        metrics={},
        findings=[],
        limitations=[],
        enrichment_hints=[],
    )
    evidence_items = build_primary_evidence(context, "service")
    event_item = next(item for item in evidence_items if item.kind == "event")

    change = _change_from_event(
        {
            "reason": "PolicyViolation",
            "message": "policy require-consistent-service-deployment-labels fail: Services must have homelab.dev/layer",
            "lastTimestamp": "2026-03-06T17:57:24Z",
            "metadata": {"namespace": "observability"},
            "involvedObject": {
                "kind": "Service",
                "namespace": "observability",
                "name": "giraffe-kube-prometheus-st-prometheus",
            },
        },
        "direct",
    )

    assert event_item.fingerprint == change.fingerprint


def test_filter_related_data_omits_duplicate_event_change() -> None:
    duplicate_fingerprint = "event|service|observability|giraffe|policyviolation|policy fail"
    report = RootCauseReport(
        scope="service",
        target="service/giraffe",
        diagnosis="No Critical Signals Found",
        likely_cause=None,
        confidence="low",
        evidence=["recent events - policy fail"],
        evidence_items=[
            EvidenceItem(
                fingerprint=duplicate_fingerprint,
                source="events",
                kind="event",
                severity="warning",
                summary="recent events",
                detail="Warning PolicyViolation policy fail",
            )
        ],
        limitations=[],
        recommended_next_step="Inspect service dashboards, recent deploys, and upstream or downstream dependencies before changing traffic handling.",
        suggested_follow_ups=[],
    )
    changes = [
        CorrelatedChange(
            fingerprint=duplicate_fingerprint,
            timestamp="2026-03-06T17:57:24Z",
            source="k8s_event",
            resource_kind="service",
            namespace="observability",
            name="giraffe",
            relation="direct",
            summary="PolicyViolation: policy fail",
            confidence="high",
        )
    ]

    filtered, note = _filter_related_data(report, changes)

    assert filtered == []
    assert note == "all correlated changes duplicated primary evidence"


def test_canonicalize_event_fingerprint_accepts_legacy_shape() -> None:
    legacy = "event|pod/crashy-abc123|backoff|restarting failed container"
    current = "event|pod|cluster|crashy-abc123|backoff|restarting failed container"

    assert canonicalize_event_fingerprint(legacy) == current
    assert canonicalize_event_fingerprint(current) == current
