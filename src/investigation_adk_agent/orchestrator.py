from dataclasses import dataclass

from investigation_service.models import (
    BuildInvestigationPlanRequest,
    EvidenceStepContract,
    HandoffActiveEvidenceBatchRequest,
    HandoffActiveEvidenceBatchResponse,
    InvestigationReport,
    InvestigationReportingRequest,
)
from investigation_service import reporting
from investigation_service.submission_materialization import materialize_submitted_step

from .evidence_collectors import ExternalEvidenceCollector


class UnsupportedCanaryFlow(ValueError):
    """Raised when the alert canary encounters a flow outside slice-one scope."""


@dataclass(slots=True)
class AlertCanaryRunResult:
    report: InvestigationReport
    markdown: str
    final_handoff: HandoffActiveEvidenceBatchResponse


def _ensure_supported_external_step(step: EvidenceStepContract) -> None:
    if step.step_id != "collect-target-evidence":
        raise UnsupportedCanaryFlow(f"unsupported external step {step.step_id}")
    if step.requested_capability != "workload_evidence_plane":
        raise UnsupportedCanaryFlow(
            f"alert canary only supports workload evidence steps, got {step.requested_capability}"
        )


def _materialize_required_submissions(
    handoff: HandoffActiveEvidenceBatchResponse,
    *,
    collector: ExternalEvidenceCollector,
) -> list:
    active_batch = handoff.active_batch
    if active_batch is None:
        raise UnsupportedCanaryFlow("handoff requested external submissions without an active batch")

    required = set(handoff.required_external_step_ids)
    submitted_steps = []
    for step in active_batch.steps:
        if step.step_id not in required:
            continue
        _ensure_supported_external_step(step)
        collected = collector.collect_for_step(step)
        if collected.step_id != step.step_id:
            raise UnsupportedCanaryFlow(
                f"collector returned step_id {collected.step_id} for required step {step.step_id}"
            )
        submitted_steps.append(
            materialize_submitted_step(
                step,
                actual_route=collected.actual_route,
                evidence_bundle=collected.evidence_bundle,
                change_candidates=collected.change_candidates,
                summary=collected.summary,
                limitations=collected.limitations,
            )
        )
    if not submitted_steps:
        raise UnsupportedCanaryFlow("external submission was required but no submitted steps were produced")
    return submitted_steps


def format_report_markdown(report: InvestigationReport) -> str:
    evidence_lines = report.evidence or ["No direct evidence recorded."]
    if report.related_data:
        related_lines = [change.summary for change in report.related_data]
    else:
        related_lines = [report.related_data_note or "No related data available."]
    limitation_lines = report.limitations or ["No explicit limitations recorded."]
    return "\n".join(
        [
            "## Diagnosis",
            report.diagnosis,
            "",
            "## Evidence",
            *[f"- {line}" for line in evidence_lines],
            "",
            "## Related Data",
            *[f"- {line}" for line in related_lines],
            "",
            "## Limitations",
            *[f"- {line}" for line in limitation_lines],
            "",
            "## Recommended next step",
            report.recommended_next_step,
        ]
    )


def _render_from_handoff(
    incident: BuildInvestigationPlanRequest,
    handoff: HandoffActiveEvidenceBatchResponse,
) -> InvestigationReport:
    request = InvestigationReportingRequest(
        **incident.model_dump(mode="python"),
        execution_context=handoff.execution_context,
    )
    return reporting.render_investigation_report(request)


def run_alert_canary(
    incident: BuildInvestigationPlanRequest,
    *,
    collector: ExternalEvidenceCollector,
    max_handoffs: int = 4,
) -> AlertCanaryRunResult:
    if not incident.alertname:
        raise UnsupportedCanaryFlow("alert canary requires alert-shaped incident input")

    handoff = reporting.handoff_active_evidence_batch(HandoffActiveEvidenceBatchRequest(incident=incident))
    for _ in range(max_handoffs):
        if handoff.next_action == "render_report":
            report = _render_from_handoff(incident, handoff)
            return AlertCanaryRunResult(
                report=report,
                markdown=format_report_markdown(report),
                final_handoff=handoff,
            )

        if handoff.next_action == "submit_external_steps":
            submitted_steps = _materialize_required_submissions(handoff, collector=collector)
            handoff = reporting.handoff_active_evidence_batch(
                HandoffActiveEvidenceBatchRequest(
                    incident=incident,
                    handoff_token=handoff.handoff_token,
                    submitted_steps=submitted_steps,
                )
            )
            continue

        if handoff.next_action == "call_handoff_again":
            handoff = reporting.handoff_active_evidence_batch(
                HandoffActiveEvidenceBatchRequest(
                    incident=incident,
                    handoff_token=handoff.handoff_token,
                )
            )
            continue

        raise UnsupportedCanaryFlow(f"unsupported handoff next_action {handoff.next_action}")

    raise UnsupportedCanaryFlow("alert canary exceeded bounded handoff limit")
