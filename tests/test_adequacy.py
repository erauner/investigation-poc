from investigation_service.adequacy import assess_target_evidence_adequacy
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

    assert assessment.outcome == "inadequate"
    assert assessment.reasons == ("no_critical_signals_found",)


def test_assess_target_evidence_adequacy_returns_inadequate_when_limitations_exist() -> None:
    assessment = assess_target_evidence_adequacy(
        target=_target(),
        artifacts=[_artifact(limitations=["logs unavailable"])],
    )

    assert assessment.outcome == "inadequate"
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
