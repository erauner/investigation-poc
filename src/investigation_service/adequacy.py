from dataclasses import dataclass
from typing import Literal

from .models import InvestigationTarget, StepArtifact

TARGET_EVIDENCE_STEP_ID = "collect-target-evidence"
NO_CRITICAL_SIGNALS_TITLE = "No Critical Signals Found"


@dataclass(frozen=True)
class EvidenceAdequacyAssessment:
    outcome: Literal["adequate", "inadequate", "not_applicable"]
    reasons: tuple[str, ...] = ()
    evaluated_step_id: str | None = None


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

    bundle = artifact.evidence_bundle
    if bundle.limitations:
        return EvidenceAdequacyAssessment(
            outcome="inadequate",
            reasons=("bundle_limitations_present",),
            evaluated_step_id=artifact.step_id,
        )
    if not bundle.findings:
        return EvidenceAdequacyAssessment(
            outcome="inadequate",
            reasons=("bundle_findings_missing",),
            evaluated_step_id=artifact.step_id,
        )
    if any(finding.title == NO_CRITICAL_SIGNALS_TITLE for finding in bundle.findings):
        return EvidenceAdequacyAssessment(
            outcome="inadequate",
            reasons=("no_critical_signals_found",),
            evaluated_step_id=artifact.step_id,
        )

    return EvidenceAdequacyAssessment(
        outcome="adequate",
        evaluated_step_id=artifact.step_id,
    )
