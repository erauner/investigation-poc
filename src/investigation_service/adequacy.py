from dataclasses import dataclass
from typing import Literal

from .models import EvidenceBundle, InvestigationTarget, StepArtifact

TARGET_EVIDENCE_STEP_ID = "collect-target-evidence"
NO_CRITICAL_SIGNALS_TITLE = "No Critical Signals Found"
_SOFT_WORKLOAD_LIMITATION_PREFIXES = (
    "metric unavailable:",
    "prometheus unavailable",
)


@dataclass(frozen=True)
class EvidenceAdequacyAssessment:
    outcome: Literal["adequate", "weak", "contradictory", "blocked", "not_applicable"]
    reasons: tuple[str, ...] = ()
    evaluated_step_id: str | None = None


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
