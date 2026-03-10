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
from .checkpointing import GraphCheckpointConfig, resolve_checkpoint_config
from .graph import invoke_investigation_graph, resume_investigation_graph
from .graph_nodes import OrchestratorRuntimeDeps
from .state import build_initial_state


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
        return report

    return report.model_copy(
        update={
            "evidence": [*report.evidence, evidence_line],
        }
    )


def _runtime_deps() -> OrchestratorRuntimeDeps:
    return OrchestratorRuntimeDeps(
        seed_context=seed_context,
        get_active_batch=get_active_batch,
        advance_batch=advance_batch,
        render_report=render_report,
        run_required_external_steps=run_required_external_steps,
        active_batch_is_render_only=_active_batch_is_render_only,
    )


def _run_orchestrated_investigation_graph(
    req: InvestigationReportRequest,
    *,
    max_batches: int = 2,
    checkpointer=None,
    checkpoint_config: GraphCheckpointConfig | None = None,
    thread_id: str | None = None,
    checkpoint_ns: str | None = None,
    checkpoint_id: str | None = None,
    interrupt_before: tuple[str, ...] | list[str] = (),
    interrupt_after: tuple[str, ...] | list[str] = (),
) -> InvestigationReport:
    incident = _incident_from_request(req)
    resolved_checkpoint_config = resolve_checkpoint_config(
        checkpoint_config=checkpoint_config,
        thread_id=thread_id,
        checkpoint_ns=checkpoint_ns,
        checkpoint_id=checkpoint_id,
        require_thread_id=checkpointer is not None,
    )
    final_state = invoke_investigation_graph(
        build_initial_state(
            req,
            incident,
            remaining_batch_budget=max_batches,
        ),
        deps=_runtime_deps(),
        checkpointer=checkpointer,
        checkpoint_config=resolved_checkpoint_config,
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
    )
    report = final_state["final_report"]
    if report is None:
        raise ValueError("orchestration graph completed without rendering a final report")
    return report


def _resume_orchestrated_investigation_graph(
    *,
    checkpointer,
    req: InvestigationReportRequest | None = None,
    checkpoint_config: GraphCheckpointConfig | None = None,
    thread_id: str | None = None,
    checkpoint_ns: str | None = None,
    checkpoint_id: str | None = None,
    interrupt_before: tuple[str, ...] | list[str] = (),
    interrupt_after: tuple[str, ...] | list[str] = (),
) -> InvestigationReport:
    resolved_checkpoint_config = resolve_checkpoint_config(
        checkpoint_config=checkpoint_config,
        thread_id=thread_id,
        checkpoint_ns=checkpoint_ns,
        checkpoint_id=checkpoint_id,
        require_thread_id=True,
    )
    final_state = resume_investigation_graph(
        deps=_runtime_deps(),
        checkpointer=checkpointer,
        checkpoint_config=resolved_checkpoint_config,
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
    )
    report = final_state["final_report"]
    if report is None:
        raise ValueError("orchestration graph resumed without rendering a final report")
    if req is None:
        return report
    return _maybe_attach_resolved_pod_context(req, report)


def run_orchestrated_investigation(
    req: InvestigationReportRequest,
    *,
    max_batches: int = 2,
) -> InvestigationReport:
    report = _run_orchestrated_investigation_graph(
        req,
        max_batches=max_batches,
    )
    return _maybe_attach_resolved_pod_context(req, report)
