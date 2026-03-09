from investigation_service.models import (
    BuildInvestigationPlanRequest,
    FindUnhealthyPodRequest,
    InvestigationPlan,
    InvestigationReport,
    InvestigationReportRequest,
)
from investigation_service.tools import find_unhealthy_pod

from .control_plane import advance_batch, get_active_batch, render_report, seed_context
from .evidence_runner import run_required_external_steps


def _incident_from_request(req: InvestigationReportRequest) -> BuildInvestigationPlanRequest:
    return BuildInvestigationPlanRequest(
        cluster=req.cluster,
        namespace=req.namespace,
        target=req.target,
        profile=req.profile,
        service_name=req.service_name,
        lookback_minutes=req.lookback_minutes,
        alertname=req.alertname,
        labels=req.labels,
        annotations=req.annotations,
        node_name=req.node_name,
        objective="auto",
    )


def _active_batch_is_render_only(plan: InvestigationPlan) -> bool:
    batch_id = plan.active_batch_id
    if batch_id is None:
        return False

    batch = next((item for item in plan.evidence_batches if item.id == batch_id), None)
    if batch is None:
        return False

    steps_by_id = {step.id: step for step in plan.steps}
    batch_steps = [steps_by_id[step_id] for step_id in batch.step_ids if step_id in steps_by_id]
    return bool(batch_steps) and all(step.category == "render" for step in batch_steps)


def _maybe_attach_resolved_pod_context(
    req: InvestigationReportRequest,
    report: InvestigationReport,
) -> InvestigationReport:
    if not req.alertname or not req.namespace or not req.target or not req.target.startswith("pod/"):
        return report
    if report.target.startswith("pod/") and "-" in report.target.split("/", 1)[1]:
        return report

    candidate = find_unhealthy_pod(
        FindUnhealthyPodRequest(
            cluster=req.cluster,
            namespace=req.namespace,
        )
    ).candidate
    if candidate is None:
        return report

    requested_name = req.target.split("/", 1)[1]
    if not candidate.name.startswith(f"{requested_name}-"):
        return report

    evidence_line = f"Resolved concrete crash-looping pod: {candidate.target}"
    if evidence_line in report.evidence:
        return report.model_copy(update={"target": candidate.target})

    return report.model_copy(
        update={
            "target": candidate.target,
            "evidence": [*report.evidence, evidence_line],
        }
    )


def run_orchestrated_investigation(
    req: InvestigationReportRequest,
    *,
    max_batches: int = 2,
) -> InvestigationReport:
    incident = _incident_from_request(req)
    execution_context = seed_context(incident, allow_bounded_fallback_execution=False)
    remaining_batch_budget = max_batches

    while True:
        if execution_context.updated_plan.active_batch_id is None:
            break
        if _active_batch_is_render_only(execution_context.updated_plan):
            break
        if remaining_batch_budget <= 0:
            raise ValueError("orchestrator stopped with non-render work still pending")

        active_batch = get_active_batch(incident, execution_context)
        if active_batch is None:
            break

        submitted_steps = run_required_external_steps(active_batch)
        if any(step.execution_mode == "external_preferred" for step in active_batch.steps) and not submitted_steps:
            raise ValueError("required external steps were not materialized")

        advance_response = advance_batch(
            incident,
            execution_context,
            submitted_steps=submitted_steps,
            batch_id=active_batch.batch_id,
        )
        execution_context = advance_response.execution_context
        remaining_batch_budget -= 1

    report = render_report(req, execution_context)
    return _maybe_attach_resolved_pod_context(req, report)
