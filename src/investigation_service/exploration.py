from dataclasses import dataclass

from .adequacy import (
    EvidenceAdequacyAssessment,
    NO_CRITICAL_SIGNALS_TITLE,
    assess_bundle_for_capability,
    is_scout_candidate,
)
from .execution_policy import BoundedExplorationPolicy, ProbeKind, bounded_exploration_policy_for_capability
from .models import EvidenceBundle, EvidenceStepContract, StepExecutionInputs, SubmittedStepArtifact, TargetRef

_METRIC_BOOKKEEPING_KEYS = {
    "profile",
    "lookback_minutes",
    "prometheus_available",
}


@dataclass(frozen=True)
class BaselineEvidenceSummary:
    target: TargetRef
    finding_titles: tuple[str, ...]
    limitations: tuple[str, ...]
    available_metric_keys: tuple[str, ...]
    event_count: int
    has_log_excerpt: bool
    enrichment_hints: tuple[str, ...]


@dataclass(frozen=True)
class ScoutHints:
    adequacy_outcome: str
    adequacy_reasons: tuple[str, ...]
    missing_findings: bool
    missing_metrics: bool
    missing_log_excerpt: bool
    contradictory_signals: bool
    blocked_by_limitations: bool
    preferred_probe_order: tuple[ProbeKind, ...]


@dataclass(frozen=True)
class ExploratoryScoutContext:
    capability: str
    step_id: str
    plane: str
    execution_inputs: StepExecutionInputs
    policy: BoundedExplorationPolicy
    baseline_assessment: EvidenceAdequacyAssessment
    baseline_summary: BaselineEvidenceSummary
    hints: ScoutHints


def build_baseline_evidence_summary(bundle: EvidenceBundle) -> BaselineEvidenceSummary:
    event_count = 0 if bundle.events == ["no related events"] else len(bundle.events)
    available_metric_keys = tuple(
        key
        for key, value in bundle.metrics.items()
        if value is not None and key not in _METRIC_BOOKKEEPING_KEYS
    )
    return BaselineEvidenceSummary(
        target=bundle.target,
        finding_titles=tuple(finding.title for finding in bundle.findings),
        limitations=tuple(bundle.limitations),
        available_metric_keys=available_metric_keys,
        event_count=event_count,
        has_log_excerpt=bool(bundle.log_excerpt.strip()),
        enrichment_hints=tuple(bundle.enrichment_hints),
    )


def build_scout_hints(
    *,
    assessment: EvidenceAdequacyAssessment,
    summary: BaselineEvidenceSummary,
    policy: BoundedExplorationPolicy,
) -> ScoutHints:
    substantive_titles = tuple(title for title in summary.finding_titles if title != NO_CRITICAL_SIGNALS_TITLE)
    return ScoutHints(
        adequacy_outcome=assessment.outcome,
        adequacy_reasons=assessment.reasons,
        missing_findings=not substantive_titles,
        missing_metrics=not summary.available_metric_keys,
        missing_log_excerpt=not summary.has_log_excerpt,
        contradictory_signals=assessment.outcome == "contradictory",
        blocked_by_limitations=assessment.outcome == "blocked",
        preferred_probe_order=policy.probe_kinds if is_scout_candidate(assessment) else (),
    )


def build_exploratory_scout_context(
    *,
    step: EvidenceStepContract,
    artifact: SubmittedStepArtifact,
) -> ExploratoryScoutContext | None:
    capability = step.requested_capability
    policy = bounded_exploration_policy_for_capability(capability)
    if capability is None or policy is None or not policy.enabled:
        return None
    bundle = artifact.evidence_bundle
    if bundle is None:
        return None
    assessment = assess_bundle_for_capability(capability, bundle=bundle)
    if not is_scout_candidate(assessment):
        return None
    summary = build_baseline_evidence_summary(bundle)
    hints = build_scout_hints(
        assessment=assessment,
        summary=summary,
        policy=policy,
    )
    return ExploratoryScoutContext(
        capability=capability,
        step_id=step.step_id,
        plane=step.plane,
        execution_inputs=step.execution_inputs,
        policy=policy,
        baseline_assessment=assessment,
        baseline_summary=summary,
        hints=hints,
    )
