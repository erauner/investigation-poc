from investigation_service.adequacy import (
    assess_node_evidence_bundle,
    assess_service_evidence_bundle,
    assess_target_evidence_adequacy,
    assess_workload_evidence_bundle,
    adequacy_rank,
    assessment_improves,
    is_scout_candidate,
    node_bundle_improves,
    service_bundle_improves,
    workload_bundle_improves,
)
from investigation_service.models import Finding, InvestigationTarget, StepArtifact, StepRouteProvenance, TargetRef


def _target(*, scope: str = "workload") -> InvestigationTarget:
    return InvestigationTarget(
        source="manual",
        scope=scope,
        cluster="erauner-home",
        namespace="default",
        requested_target="deployment/api",
        target="deployment/api",
        service_name="api" if scope == "workload" else None,
        profile="workload" if scope == "workload" else "service",
        lookback_minutes=15,
        normalization_notes=[],
    )


def _artifact(*, findings: list[Finding] | None = None, limitations: list[str] | None = None) -> StepArtifact:
    return StepArtifact(
        step_id="collect-target-evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        summary=[],
        limitations=limitations or [],
        evidence_bundle={
            "cluster": "erauner-home",
            "target": TargetRef(namespace="default", kind="pod", name="api"),
            "object_state": {"kind": "pod", "name": "api"},
            "events": [],
            "log_excerpt": "",
            "metrics": {},
            "findings": findings or [],
            "limitations": limitations or [],
            "enrichment_hints": [],
        },
        route_provenance=StepRouteProvenance(
            requested_capability="workload_evidence_plane",
            actual_route={"source_kind": "investigation_internal"},
        ),
    )


def test_assess_target_evidence_adequacy_returns_adequate_for_strong_workload_findings() -> None:
    assessment = assess_target_evidence_adequacy(
        target=_target(),
        artifacts=[
            _artifact(
                findings=[
                    Finding(
                        severity="critical",
                        source="k8s",
                        title="CrashLoopBackOff",
                        evidence="pod is crash looping",
                    )
                ]
            )
        ],
    )

    assert assessment.outcome == "adequate"
    assert assessment.evaluated_step_id == "collect-target-evidence"


def test_assess_target_evidence_adequacy_returns_inadequate_for_no_critical_signals() -> None:
    assessment = assess_target_evidence_adequacy(
        target=_target(),
        artifacts=[
            _artifact(
                findings=[
                    Finding(
                        severity="info",
                        source="heuristic",
                        title="No Critical Signals Found",
                        evidence="nothing decisive",
                    )
                ]
            )
        ],
    )

    assert assessment.outcome == "weak"
    assert assessment.reasons == ("no_critical_signals_found",)


def test_assess_target_evidence_adequacy_returns_inadequate_when_limitations_exist() -> None:
    assessment = assess_target_evidence_adequacy(
        target=_target(),
        artifacts=[_artifact(limitations=["logs unavailable"])],
    )

    assert assessment.outcome == "blocked"
    assert assessment.reasons == ("bundle_limitations_present", "bundle_findings_missing")


def test_assess_workload_evidence_bundle_returns_contradictory_for_conflicting_findings() -> None:
    assessment = assess_workload_evidence_bundle(
        bundle=_artifact(
            findings=[
                Finding(
                    severity="info",
                    source="heuristic",
                    title="No Critical Signals Found",
                    evidence="nothing decisive",
                ),
                Finding(
                    severity="critical",
                    source="k8s",
                    title="CrashLoopBackOff",
                    evidence="pod is crash looping",
                ),
            ]
        ).evidence_bundle
    )

    assert assessment.outcome == "contradictory"
    assert assessment.reasons == ("no_critical_signals_conflicts_with_other_findings",)


def test_assess_workload_evidence_bundle_returns_weak_for_limited_but_non_empty_findings() -> None:
    assessment = assess_workload_evidence_bundle(
        bundle=_artifact(
            findings=[
                Finding(
                    severity="warning",
                    source="k8s",
                    title="CrashLoopBackOff",
                    evidence="pod is crash looping",
                )
            ],
            limitations=["peer logs truncated"],
        ).evidence_bundle
    )

    assert assessment.outcome == "weak"
    assert assessment.reasons == ("bundle_limitations_present",)


def test_assess_target_evidence_adequacy_returns_not_applicable_for_irrelevant_inputs() -> None:
    assert assess_target_evidence_adequacy(target=None, artifacts=[]).outcome == "not_applicable"
    assert assess_target_evidence_adequacy(target=_target(scope="service"), artifacts=[]).outcome == "not_applicable"
    assert assess_target_evidence_adequacy(
        target=_target(),
        artifacts=[
            StepArtifact(
                step_id="collect-change-candidates",
                plane="changes",
                artifact_type="change_candidates",
                summary=[],
                limitations=[],
                route_provenance=None,
            )
        ],
    ).outcome == "not_applicable"


def test_adequacy_helpers_define_ordering_and_scout_trigger_rules() -> None:
    blocked = assess_workload_evidence_bundle(bundle=_artifact(limitations=["logs unavailable"]).evidence_bundle)
    weak = assess_workload_evidence_bundle(
        bundle=_artifact(
            findings=[
                Finding(
                    severity="info",
                    source="heuristic",
                    title="No Critical Signals Found",
                    evidence="nothing decisive",
                )
            ]
        ).evidence_bundle
    )
    contradictory = assess_workload_evidence_bundle(
        bundle=_artifact(
            findings=[
                Finding(
                    severity="info",
                    source="heuristic",
                    title="No Critical Signals Found",
                    evidence="nothing decisive",
                ),
                Finding(
                    severity="warning",
                    source="k8s",
                    title="CrashLoopBackOff",
                    evidence="pod is crash looping",
                ),
            ]
        ).evidence_bundle
    )
    adequate = assess_workload_evidence_bundle(
        bundle=_artifact(
            findings=[
                Finding(
                    severity="critical",
                    source="k8s",
                    title="CrashLoopBackOff",
                    evidence="pod is crash looping",
                )
            ]
        ).evidence_bundle
    )

    assert adequacy_rank(blocked.outcome) < adequacy_rank(weak.outcome) < adequacy_rank(contradictory.outcome) < adequacy_rank(adequate.outcome)
    assert is_scout_candidate(blocked) is True
    assert is_scout_candidate(weak) is True
    assert is_scout_candidate(contradictory) is True
    assert is_scout_candidate(adequate) is False
    assert assessment_improves(blocked, weak) is True
    assert assessment_improves(weak, adequate) is True
    assert assessment_improves(contradictory, weak) is False


def test_workload_bundle_improves_prefers_stronger_findings_with_same_adequacy_bucket() -> None:
    baseline = _artifact(
        findings=[
            Finding(
                severity="info",
                source="heuristic",
                title="No Critical Signals Found",
                evidence="nothing decisive",
            )
        ],
        limitations=["logs unavailable"],
    ).evidence_bundle
    candidate = _artifact(
        findings=[
            Finding(
                severity="warning",
                source="events",
                title="Crash Loop Detected",
                evidence="backoff seen in events",
            )
        ],
        limitations=["logs unavailable"],
    ).evidence_bundle

    assert assess_workload_evidence_bundle(bundle=baseline).outcome == "weak"
    assert assess_workload_evidence_bundle(bundle=candidate).outcome == "weak"
    assert workload_bundle_improves(baseline, candidate) is True


def test_assess_service_evidence_bundle_classifies_missing_metrics_as_blocked() -> None:
    bundle = _artifact(limitations=["prometheus unavailable or returned no usable results"]).evidence_bundle.model_copy(
        update={
            "metrics": {
                "service_request_rate": None,
                "service_error_rate": None,
                "service_latency_p95_seconds": None,
                "prometheus_available": False,
            }
        }
    )

    assessment = assess_service_evidence_bundle(bundle=bundle)

    assert assessment.outcome == "blocked"


def test_service_bundle_improves_prefers_recovered_prometheus_signals() -> None:
    baseline = _artifact(
        findings=[
            Finding(
                severity="info",
                source="heuristic",
                title="No Critical Signals Found",
                evidence="nothing decisive",
            )
        ],
        limitations=["prometheus unavailable or returned no usable results"],
    ).evidence_bundle.model_copy(
        update={
            "metrics": {
                "service_request_rate": None,
                "service_error_rate": None,
                "service_latency_p95_seconds": None,
                "prometheus_available": False,
            }
        }
    )
    candidate = baseline.model_copy(
        update={
            "findings": [
                Finding(
                    severity="warning",
                    source="prometheus",
                    title="High Service Latency",
                    evidence="p95 latency is 1.200s",
                )
            ],
            "limitations": [],
            "metrics": {
                "service_request_rate": 12.5,
                "service_error_rate": 0.5,
                "service_latency_p95_seconds": 1.2,
                "prometheus_available": True,
            },
        }
    )

    assert service_bundle_improves(baseline, candidate) is True


def test_assess_node_evidence_bundle_returns_adequate_for_not_ready_signal() -> None:
    bundle = _artifact(
        findings=[
            Finding(
                severity="critical",
                source="k8s",
                title="Node Not Ready",
                evidence="Node condition Ready=False",
            )
        ]
    ).evidence_bundle

    assessment = assess_node_evidence_bundle(bundle=bundle)

    assert assessment.outcome == "adequate"


def test_assess_node_evidence_bundle_returns_weak_for_request_saturation_only() -> None:
    bundle = _artifact(
        findings=[
            Finding(
                severity="warning",
                source="prometheus",
                title="High Node Memory Request Saturation",
                evidence="Memory requests are at 90.0% of allocatable capacity",
            )
        ]
    ).evidence_bundle

    assessment = assess_node_evidence_bundle(bundle=bundle)

    assert assessment.outcome == "weak"
    assert assessment.reasons == ("request_saturation_only",)


def test_assess_node_evidence_bundle_returns_blocked_for_missing_metrics_and_limitations() -> None:
    bundle = _artifact(limitations=["prometheus unavailable or returned no usable results"]).evidence_bundle.model_copy(
        update={
            "metrics": {
                "node_memory_allocatable_bytes": None,
                "node_memory_working_set_bytes": None,
                "node_memory_request_bytes": None,
                "prometheus_available": False,
            }
        }
    )

    assessment = assess_node_evidence_bundle(bundle=bundle)

    assert assessment.outcome == "blocked"


def test_node_bundle_improves_prefers_added_top_pod_summary() -> None:
    baseline = _artifact(
        findings=[
            Finding(
                severity="warning",
                source="prometheus",
                title="High Node Memory Request Saturation",
                evidence="Memory requests are at 90.0% of allocatable capacity",
            )
        ],
        limitations=[],
    ).evidence_bundle.model_copy(
        update={
            "target": TargetRef(namespace=None, kind="node", name="worker3"),
            "object_state": {"kind": "node", "name": "worker3", "conditions": []},
            "metrics": {
                "node_memory_allocatable_bytes": 100.0,
                "node_memory_working_set_bytes": 40.0,
                "node_memory_request_bytes": 90.0,
                "prometheus_available": True,
            },
        }
    )
    candidate = baseline.model_copy(
        update={
            "object_state": {
                "kind": "node",
                "name": "worker3",
                "conditions": [],
                "top_pods_by_memory_request": [
                    {"namespace": "operator-smoke", "name": "api-0", "memory_request_bytes": 536870912}
                ],
            }
        }
    )

    assert node_bundle_improves(baseline, candidate) is True
