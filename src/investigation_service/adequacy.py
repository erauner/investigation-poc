from dataclasses import dataclass
from typing import Literal

from .models import EvidenceBundle, InvestigationTarget, StepArtifact

TARGET_EVIDENCE_STEP_ID = "collect-target-evidence"
NO_CRITICAL_SIGNALS_TITLE = "No Critical Signals Found"
_SOFT_WORKLOAD_LIMITATION_PREFIXES = (
    "metric unavailable:",
    "prometheus unavailable",
)
_ADEQUACY_RANKS = {
    "adequate": 4,
    "contradictory": 3,
    "weak": 2,
    "blocked": 1,
    "not_applicable": 0,
}


@dataclass(frozen=True)
class EvidenceAdequacyAssessment:
    outcome: Literal["adequate", "weak", "contradictory", "blocked", "not_applicable"]
    reasons: tuple[str, ...] = ()
    evaluated_step_id: str | None = None


def adequacy_rank(outcome: Literal["adequate", "weak", "contradictory", "blocked", "not_applicable"]) -> int:
    return _ADEQUACY_RANKS[outcome]


def is_scout_candidate(assessment: EvidenceAdequacyAssessment) -> bool:
    return adequacy_rank(assessment.outcome) < adequacy_rank("adequate")


def assessment_improves(
    baseline: EvidenceAdequacyAssessment,
    candidate: EvidenceAdequacyAssessment,
) -> bool:
    return adequacy_rank(candidate.outcome) > adequacy_rank(baseline.outcome)


def workload_bundle_quality_key(bundle: EvidenceBundle | None) -> tuple[int, int, int]:
    if bundle is None:
        return (0, 0, 0)
    hard_limitations = tuple(
        item
        for item in bundle.limitations
        if not item.startswith(_SOFT_WORKLOAD_LIMITATION_PREFIXES)
    )
    substantive_findings = sum(1 for finding in bundle.findings if finding.title != NO_CRITICAL_SIGNALS_TITLE)
    has_no_critical_signals = any(finding.title == NO_CRITICAL_SIGNALS_TITLE for finding in bundle.findings)
    return (
        substantive_findings,
        -len(hard_limitations),
        0 if has_no_critical_signals else 1,
    )


def workload_bundle_improves(baseline: EvidenceBundle | None, candidate: EvidenceBundle | None) -> bool:
    return workload_bundle_quality_key(candidate) > workload_bundle_quality_key(baseline)


def service_bundle_quality_key(bundle: EvidenceBundle | None) -> tuple[int, int, int]:
    if bundle is None:
        return (0, 0, 0)
    hard_limitations = tuple(
        item
        for item in bundle.limitations
        if item != "no related Kubernetes events found"
    )
    substantive_findings = sum(1 for finding in bundle.findings if finding.title != NO_CRITICAL_SIGNALS_TITLE)
    usable_prometheus_signals = sum(
        1
        for key in ("service_request_rate", "service_error_rate", "service_latency_p95_seconds")
        if bundle.metrics.get(key) is not None
    )
    return (
        substantive_findings,
        usable_prometheus_signals,
        -len(hard_limitations),
    )


def service_bundle_improves(baseline: EvidenceBundle | None, candidate: EvidenceBundle | None) -> bool:
    return service_bundle_quality_key(candidate) > service_bundle_quality_key(baseline)


def assess_workload_evidence_bundle(
    *,
    bundle: EvidenceBundle | None,
) -> EvidenceAdequacyAssessment:
    if bundle is None:
        return EvidenceAdequacyAssessment(outcome="not_applicable")

    has_no_critical_signals = any(finding.title == NO_CRITICAL_SIGNALS_TITLE for finding in bundle.findings)
    has_other_findings = any(finding.title != NO_CRITICAL_SIGNALS_TITLE for finding in bundle.findings)
    hard_limitations = tuple(
        item
        for item in bundle.limitations
        if not item.startswith(_SOFT_WORKLOAD_LIMITATION_PREFIXES)
    )

    if hard_limitations and not bundle.findings:
        return EvidenceAdequacyAssessment(
            outcome="blocked",
            reasons=("bundle_limitations_present", "bundle_findings_missing"),
        )
    if has_no_critical_signals and has_other_findings:
        return EvidenceAdequacyAssessment(
            outcome="contradictory",
            reasons=("no_critical_signals_conflicts_with_other_findings",),
        )
    if not bundle.findings:
        return EvidenceAdequacyAssessment(
            outcome="weak",
            reasons=("bundle_findings_missing",),
        )
    if has_no_critical_signals:
        return EvidenceAdequacyAssessment(
            outcome="weak",
            reasons=("no_critical_signals_found",),
        )
    if hard_limitations:
        return EvidenceAdequacyAssessment(
            outcome="weak",
            reasons=("bundle_limitations_present",),
        )
    return EvidenceAdequacyAssessment(outcome="adequate")


def assess_service_evidence_bundle(
    *,
    bundle: EvidenceBundle | None,
) -> EvidenceAdequacyAssessment:
    if bundle is None:
        return EvidenceAdequacyAssessment(outcome="not_applicable")

    has_no_critical_signals = any(finding.title == NO_CRITICAL_SIGNALS_TITLE for finding in bundle.findings)
    has_other_findings = any(finding.title != NO_CRITICAL_SIGNALS_TITLE for finding in bundle.findings)
    hard_limitations = tuple(
        item
        for item in bundle.limitations
        if item != "no related Kubernetes events found"
    )
    usable_prometheus_signals = any(
        bundle.metrics.get(key) is not None
        for key in ("service_request_rate", "service_error_rate", "service_latency_p95_seconds")
    )

    if hard_limitations and not bundle.findings and not usable_prometheus_signals:
        return EvidenceAdequacyAssessment(
            outcome="blocked",
            reasons=("bundle_limitations_present", "bundle_findings_missing"),
        )
    if has_no_critical_signals and has_other_findings:
        return EvidenceAdequacyAssessment(
            outcome="contradictory",
            reasons=("no_critical_signals_conflicts_with_other_findings",),
        )
    if not bundle.findings:
        return EvidenceAdequacyAssessment(
            outcome="weak",
            reasons=("bundle_findings_missing",),
        )
    if has_no_critical_signals:
        return EvidenceAdequacyAssessment(
            outcome="weak",
            reasons=("no_critical_signals_found",),
        )
    if hard_limitations:
        return EvidenceAdequacyAssessment(
            outcome="weak",
            reasons=("bundle_limitations_present",),
        )
    return EvidenceAdequacyAssessment(outcome="adequate")


def assess_target_evidence_adequacy(
    *,
    target: InvestigationTarget | None,
    artifacts: list[StepArtifact],
) -> EvidenceAdequacyAssessment:
    if target is None or target.scope != "workload":
        return EvidenceAdequacyAssessment(outcome="not_applicable")

    artifact = next((item for item in artifacts if item.step_id == TARGET_EVIDENCE_STEP_ID), None)
    if artifact is None:
        return EvidenceAdequacyAssessment(outcome="not_applicable")

    if artifact.evidence_bundle is None:
        return EvidenceAdequacyAssessment(
            outcome="not_applicable",
            reasons=("target_evidence_bundle_missing",),
            evaluated_step_id=artifact.step_id,
        )

    assessment = assess_workload_evidence_bundle(bundle=artifact.evidence_bundle)
    return EvidenceAdequacyAssessment(
        outcome=assessment.outcome,
        reasons=assessment.reasons,
        evaluated_step_id=artifact.step_id,
    )
