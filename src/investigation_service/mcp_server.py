import os

from mcp.server.fastmcp import FastMCP

from .mcp_logging import run_logged_tool
from .models import (
    AdvanceInvestigationRuntimeRequest,
    BuildInvestigationPlanRequest,
    CollectAlertContextRequest,
    CollectCorrelatedChangesRequest,
    CollectContextRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    ExecuteInvestigationStepRequest,
    FindUnhealthyPodRequest,
    FindUnhealthyWorkloadsRequest,
    GetActiveEvidenceBatchRequest,
    HandoffActiveEvidenceBatchRequest,
    InvestigationReportingRequest,
    InvestigationReportRequest,
    SubmitEvidenceArtifactsRequest,
    UpdateInvestigationPlanRequest,
)
from .reporting import advance_investigation_runtime as advance_investigation_runtime_impl
from .correlation import collect_change_candidates as collect_change_candidates_impl
from .reporting import build_investigation_plan as build_investigation_plan_impl
from .reporting import execute_investigation_step as execute_investigation_step_impl
from .reporting import get_active_evidence_batch as get_active_evidence_batch_impl
from .reporting import handoff_active_evidence_batch as handoff_active_evidence_batch_impl
from .reporting import rank_hypotheses as rank_hypotheses_impl
from .reporting import render_investigation_report as render_investigation_report_impl
from .reporting import resolve_primary_target as resolve_primary_target_impl
from .reporting import submit_evidence_step_artifacts as submit_evidence_step_artifacts_impl
from .reporting import update_investigation_plan as update_investigation_plan_impl
from investigation_orchestrator.entrypoint import run_orchestrated_investigation as run_orchestrated_investigation_impl
from .tools import find_unhealthy_pod as find_unhealthy_pod_impl
from .tools import find_unhealthy_workloads as find_unhealthy_workloads_impl
from .tools import normalize_alert_input as normalize_alert_input_impl

mcp = FastMCP(
    "investigation-mcp-server",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8001")),
    streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
)


@mcp.tool()
def normalize_alert_input(
    alertname: str,
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    cluster: str | None = None,
    namespace: str | None = None,
    node_name: str | None = None,
    target: str | None = None,
    profile: str = "workload",
    service_name: str | None = None,
    lookback_minutes: int = 15,
) -> dict:
    """Normalize alert-shaped input into a typed investigation request without collecting data. Use mainly for debugging or explicit routing inspection."""
    return run_logged_tool(
        "normalize_alert_input",
        {
            "alertname": alertname,
            "labels": labels or {},
            "annotations": annotations or {},
            "cluster": cluster,
            "namespace": namespace,
            "node_name": node_name,
            "target": target,
            "profile": profile,
            "service_name": service_name,
            "lookback_minutes": lookback_minutes,
        },
        lambda: normalize_alert_input_impl(
            CollectAlertContextRequest(
                alertname=alertname,
                labels=labels or {},
                annotations=annotations or {},
                cluster=cluster,
                namespace=namespace,
                node_name=node_name,
                target=target,
                profile=profile,
                service_name=service_name,
                lookback_minutes=lookback_minutes,
            )
        ).model_dump(mode="json")
    )


@mcp.tool()
def resolve_primary_target(
    target: str | None = None,
    cluster: str | None = None,
    namespace: str | None = None,
    profile: str = "workload",
    service_name: str | None = None,
    lookback_minutes: int = 15,
    alertname: str | None = None,
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    node_name: str | None = None,
) -> dict:
    """Resolve the primary investigation target, including convenience targets and vague workload expansion, without collecting evidence."""
    return run_logged_tool(
        "resolve_primary_target",
        {
            "target": target,
            "cluster": cluster,
            "namespace": namespace,
            "profile": profile,
            "service_name": service_name,
            "lookback_minutes": lookback_minutes,
            "alertname": alertname,
            "labels": labels or {},
            "annotations": annotations or {},
            "node_name": node_name,
        },
        lambda: resolve_primary_target_impl(
            InvestigationReportRequest(
                cluster=cluster,
                namespace=namespace,
                target=target,
                profile=profile,
                service_name=service_name,
                lookback_minutes=lookback_minutes,
                alertname=alertname,
                labels=labels or {},
                annotations=annotations or {},
                node_name=node_name,
            )
        ).model_dump(mode="json")
    )


@mcp.tool()
def build_investigation_plan(
    target: str | None = None,
    cluster: str | None = None,
    namespace: str | None = None,
    profile: str = "workload",
    service_name: str | None = None,
    lookback_minutes: int = 15,
    alertname: str | None = None,
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    node_name: str | None = None,
    objective: str = "auto",
    question: str | None = None,
) -> dict:
    """Build an explicit investigation plan without collecting evidence. Typically call this once before advance_investigation_runtime."""
    return run_logged_tool(
        "build_investigation_plan",
        {
            "target": target,
            "cluster": cluster,
            "namespace": namespace,
            "profile": profile,
            "service_name": service_name,
            "lookback_minutes": lookback_minutes,
            "alertname": alertname,
            "labels": labels or {},
            "annotations": annotations or {},
            "node_name": node_name,
            "objective": objective,
            "question": question,
        },
        lambda: build_investigation_plan_impl(
            BuildInvestigationPlanRequest(
                cluster=cluster,
                namespace=namespace,
                target=target,
                profile=profile,
                service_name=service_name,
                lookback_minutes=lookback_minutes,
                alertname=alertname,
                labels=labels or {},
                annotations=annotations or {},
                node_name=node_name,
                objective=objective,
                question=question,
            )
        ).model_dump(mode="json")
    )


@mcp.tool()
def execute_investigation_step(
    plan: dict,
    incident: dict,
    batch_id: str | None = None,
) -> dict:
    """Execute the remaining pending steps in one bounded evidence batch. This is a lower-level bounded fallback/debug primitive; prefer advance_investigation_runtime for normal orchestration."""
    return run_logged_tool(
        "execute_investigation_step",
        {"plan": plan, "incident": incident, "batch_id": batch_id},
        lambda: execute_investigation_step_impl(
            ExecuteInvestigationStepRequest(
                plan=plan,
                incident=incident,
                batch_id=batch_id,
            )
        ).model_dump(mode="json")
    )


@mcp.tool()
def get_active_evidence_batch(plan: dict, incident: dict, batch_id: str | None = None) -> dict:
    """Expose the current bounded evidence batch as an execution-facing contract for the remaining pending steps.

    Required call shape:
    - plan=<the full build_investigation_plan result or updated plan>
    - incident=<the same request shape used to build the plan>
    - batch_id=<optional explicit batch id>

    Do not call this tool with only batch_id.
    """
    return run_logged_tool(
        "get_active_evidence_batch",
        {"plan": plan, "incident": incident, "batch_id": batch_id},
        lambda: get_active_evidence_batch_impl(
            GetActiveEvidenceBatchRequest(
                plan=plan,
                incident=incident,
                batch_id=batch_id,
            )
        ).model_dump(mode="json")
    )


@mcp.tool()
def submit_evidence_step_artifacts(
    plan: dict,
    incident: dict,
    submitted_steps: list[dict],
    batch_id: str | None = None,
) -> dict:
    """Submit externally gathered artifacts for externally satisfiable pending steps and reconcile them into the planner-owned control plane.

    Required call shape:
    - plan=<the full build_investigation_plan result or updated plan>
    - incident=<the same request shape used to build the plan>
    - submitted_steps=<typed artifacts for externally satisfiable pending steps from get_active_evidence_batch>
    - batch_id=<optional explicit batch id>

    Constraints:
    - each submitted_steps item must be fully materialized for the matching step artifact_type
    - metadata-only workload peer-failure submissions are invalid here
    - use advance_investigation_runtime or handoff_active_evidence_batch for workload peer-failure fallback

    Use this after get_active_evidence_batch and before advance_investigation_runtime when the active batch still requires external evidence submission.
    """
    return run_logged_tool(
        "submit_evidence_step_artifacts",
        {
            "plan": plan,
            "incident": incident,
            "submitted_steps": submitted_steps,
            "batch_id": batch_id,
        },
        lambda: submit_evidence_step_artifacts_impl(
            SubmitEvidenceArtifactsRequest(
                plan=plan,
                incident=incident,
                batch_id=batch_id,
                submitted_steps=submitted_steps,
            )
        ).model_dump(mode="json")
    )


@mcp.tool()
def advance_investigation_runtime(
    incident: dict,
    execution_context: dict | None = None,
    submitted_steps: list[dict] | None = None,
    batch_id: str | None = None,
) -> dict:
    """Advance exactly one active evidence batch.

    Required call shape:
    - incident=<the same request shape used to build the plan>
    - execution_context=<seeded from the built plan or carried forward from a prior advance>
    - submitted_steps=<optional typed artifacts for any externally satisfied pending steps in this batch>
    - batch_id=<optional explicit batch id>

    Transition behavior:
    - metadata-only workload submissions are accepted here when peer MCP workload collection was attempted but failed
    - include step_id, actual_route for the attempted peer route, and limitations describing the failure
    - omit evidence_bundle so planner-owned bounded fallback can execute for that workload step

    Do not call this tool with only batch_id.
    Prefer this only after external-preferred steps for the active batch have already been submitted, or when the batch is planner-owned only.
    """
    return run_logged_tool(
        "advance_investigation_runtime",
        {
            "incident": incident,
            "execution_context": execution_context,
            "submitted_steps": submitted_steps or [],
            "batch_id": batch_id,
        },
        lambda: advance_investigation_runtime_impl(
            AdvanceInvestigationRuntimeRequest(
                incident=incident,
                execution_context=execution_context,
                submitted_steps=submitted_steps or [],
                batch_id=batch_id,
            )
        ).model_dump(mode="json")
    )


@mcp.tool()
def handoff_active_evidence_batch(
    incident: dict,
    execution_context: dict | None = None,
    handoff_token: str | None = None,
    submitted_steps: list[dict] | None = None,
    batch_id: str | None = None,
) -> dict:
    """Preferred agent-facing runtime helper for one bounded evidence-batch handoff.

    Required call shape:
    - incident=<the same request shape used to build the plan>
    - first call: omit both execution_context and handoff_token
    - follow-up calls: prefer handoff_token=<the opaque token returned by the previous handoff response>
    - execution_context=<legacy optional seeded or carried-forward runtime context; prefer handoff_token for handoff continuation>
    - submitted_steps=<optional typed artifacts for externally satisfied pending steps>
    - batch_id=<optional explicit batch id>

    Behavior:
    - on the first call, returns a response with handoff_status=awaiting_external_submission, next_action=submit_external_steps, and required_external_step_ids when peer evidence is still required
    - when next_action=submit_external_steps, build submitted_steps from the matching required_external_step_ids in active_batch.steps
    - each submitted_steps item should include step_id=<the step contract id>, actual_route=<the peer MCP route actually used or attempted>, and the payload field named by that step's artifact_type
    - for workload-only peer failure during transition, a submitted_steps item may carry only step_id, actual_route, and limitations to record the failed peer attempt before planner-owned bounded fallback runs
    - metadata-only workload submissions are intended for handoff_active_evidence_batch and advance_investigation_runtime; submit_evidence_step_artifacts still expects fully materialized artifacts
    - do not call handoff_active_evidence_batch again with an empty submitted_steps list after next_action=submit_external_steps
    - after submitted_steps are provided, reconciles them, auto-runs only remaining same-batch planner-owned steps, and returns updated execution_context plus a refreshed handoff_token
    - if another planner-owned batch remains, returns handoff_status=ready_for_next_handoff and next_action=call_handoff_again
    - if no more evidence batches remain, returns handoff_status=complete and next_action=render_report

    Prefer this over manually choreographing get_active_evidence_batch, submit_evidence_step_artifacts, and advance_investigation_runtime.
    Follow next_action directly instead of inferring the next step only from active_batch.
    """
    return run_logged_tool(
        "handoff_active_evidence_batch",
        {
            "incident": incident,
            "execution_context": execution_context,
            "handoff_token": handoff_token,
            "submitted_steps": submitted_steps or [],
            "batch_id": batch_id,
        },
        lambda: handoff_active_evidence_batch_impl(
            HandoffActiveEvidenceBatchRequest(
                incident=incident,
                execution_context=execution_context,
                handoff_token=handoff_token,
                submitted_steps=submitted_steps or [],
                batch_id=batch_id,
            )
        ).model_dump(mode="json")
    )


@mcp.tool()
def update_investigation_plan(plan: dict, execution: dict) -> dict:
    """Update plan state after one executed evidence batch. This is a lower-level fallback/debug primitive; prefer advance_investigation_runtime for normal orchestration."""
    return run_logged_tool(
        "update_investigation_plan",
        {"plan": plan, "execution": execution},
        lambda: update_investigation_plan_impl(
            UpdateInvestigationPlanRequest(
                plan=plan,
                execution=execution,
            )
        ).model_dump(mode="json")
    )


@mcp.tool()
def find_unhealthy_workloads(namespace: str, limit: int = 5, cluster: str | None = None) -> dict:
    """List concrete unhealthy pod targets in a namespace for debugging or exploratory routing inspection. Prefer resolve_primary_target for the planner-led path."""
    return run_logged_tool(
        "find_unhealthy_workloads",
        {"cluster": cluster, "namespace": namespace, "limit": limit},
        lambda: find_unhealthy_workloads_impl(
            FindUnhealthyWorkloadsRequest(cluster=cluster, namespace=namespace, limit=limit)
        ).model_dump(mode="json")
    )


@mcp.tool()
def find_unhealthy_pod(namespace: str, cluster: str | None = None) -> dict:
    """Find the single best unhealthy pod candidate in a namespace for debugging or exploratory routing inspection. Prefer resolve_primary_target for the planner-led path."""
    return run_logged_tool(
        "find_unhealthy_pod",
        {"cluster": cluster, "namespace": namespace},
        lambda: find_unhealthy_pod_impl(
            FindUnhealthyPodRequest(cluster=cluster, namespace=namespace)
        ).model_dump(mode="json"),
    )


@mcp.tool()
def collect_change_candidates(
    target: str,
    cluster: str | None = None,
    namespace: str | None = None,
    profile: str = "workload",
    service_name: str | None = None,
    lookback_minutes: int = 60,
    anchor_timestamp: str | None = None,
    limit: int = 10,
) -> dict:
    """Collect ranked change candidates related to the current investigation target."""
    return run_logged_tool(
        "collect_change_candidates",
        {
            "target": target,
            "cluster": cluster,
            "namespace": namespace,
            "profile": profile,
            "service_name": service_name,
            "lookback_minutes": lookback_minutes,
            "anchor_timestamp": anchor_timestamp,
            "limit": limit,
        },
        lambda: collect_change_candidates_impl(
            CollectCorrelatedChangesRequest(
                cluster=cluster,
                namespace=namespace,
                target=target,
                profile=profile,
                service_name=service_name,
                lookback_minutes=lookback_minutes,
                anchor_timestamp=anchor_timestamp,
                limit=limit,
            )
        ).model_dump(mode="json")
    )


@mcp.tool()
def rank_hypotheses(
    target: str | None = None,
    cluster: str | None = None,
    namespace: str | None = None,
    profile: str = "workload",
    service_name: str | None = None,
    lookback_minutes: int = 15,
    alertname: str | None = None,
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    node_name: str | None = None,
    execution_context: dict | None = None,
) -> dict:
    """Analyze collected investigation evidence and return ranked hypotheses without rendering the final report."""
    return run_logged_tool(
        "rank_hypotheses",
        {
            "target": target,
            "cluster": cluster,
            "namespace": namespace,
            "profile": profile,
            "service_name": service_name,
            "lookback_minutes": lookback_minutes,
            "alertname": alertname,
            "labels": labels or {},
            "annotations": annotations or {},
            "node_name": node_name,
            "execution_context": execution_context,
        },
        lambda: rank_hypotheses_impl(
            InvestigationReportingRequest(
                cluster=cluster,
                namespace=namespace,
                target=target,
                profile=profile,
                service_name=service_name,
                lookback_minutes=lookback_minutes,
                alertname=alertname,
                labels=labels or {},
                annotations=annotations or {},
                node_name=node_name,
                execution_context=execution_context,
            )
        ).model_dump(mode="json")
    )


@mcp.tool()
def render_investigation_report(
    target: str | None = None,
    cluster: str | None = None,
    namespace: str | None = None,
    profile: str = "workload",
    service_name: str | None = None,
    lookback_minutes: int = 15,
    include_related_data: bool = True,
    correlation_window_minutes: int = 60,
    correlation_limit: int = 10,
    anchor_timestamp: str | None = None,
    alertname: str | None = None,
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    node_name: str | None = None,
    execution_context: dict | None = None,
) -> dict:
    """Render the final investigation report from the staged artifact-oriented pipeline. Prefer passing execution_context from advance_investigation_runtime when available."""
    return run_logged_tool(
        "render_investigation_report",
        {
            "target": target,
            "cluster": cluster,
            "namespace": namespace,
            "profile": profile,
            "service_name": service_name,
            "lookback_minutes": lookback_minutes,
            "include_related_data": include_related_data,
            "correlation_window_minutes": correlation_window_minutes,
            "correlation_limit": correlation_limit,
            "anchor_timestamp": anchor_timestamp,
            "alertname": alertname,
            "labels": labels or {},
            "annotations": annotations or {},
            "node_name": node_name,
            "execution_context": execution_context,
        },
        lambda: render_investigation_report_impl(
            InvestigationReportingRequest(
                cluster=cluster,
                namespace=namespace,
                target=target,
                profile=profile,
                service_name=service_name,
                lookback_minutes=lookback_minutes,
                include_related_data=include_related_data,
                correlation_window_minutes=correlation_window_minutes,
                correlation_limit=correlation_limit,
                anchor_timestamp=anchor_timestamp,
                alertname=alertname,
                labels=labels or {},
                annotations=annotations or {},
                node_name=node_name,
                execution_context=execution_context,
            )
        ).model_dump(mode="json")
    )


@mcp.tool()
def run_orchestrated_investigation(
    target: str | None = None,
    cluster: str | None = None,
    namespace: str | None = None,
    profile: str = "workload",
    service_name: str | None = None,
    lookback_minutes: int = 15,
    include_related_data: bool = True,
    correlation_window_minutes: int = 60,
    correlation_limit: int = 10,
    anchor_timestamp: str | None = None,
    alertname: str | None = None,
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    node_name: str | None = None,
) -> dict:
    """Run the bounded investigation orchestration loop in product code and return the final report.

    Prefer this for the normal planner-led happy path. It keeps batch selection, external-step
    materialization, advancement, and final rendering in code instead of prompt choreography.
    """
    return run_logged_tool(
        "run_orchestrated_investigation",
        {
            "target": target,
            "cluster": cluster,
            "namespace": namespace,
            "profile": profile,
            "service_name": service_name,
            "lookback_minutes": lookback_minutes,
            "include_related_data": include_related_data,
            "correlation_window_minutes": correlation_window_minutes,
            "correlation_limit": correlation_limit,
            "anchor_timestamp": anchor_timestamp,
            "alertname": alertname,
            "labels": labels or {},
            "annotations": annotations or {},
            "node_name": node_name,
        },
        lambda: run_orchestrated_investigation_impl(
            InvestigationReportRequest(
                cluster=cluster,
                namespace=namespace,
                target=target,
                profile=profile,
                service_name=service_name,
                lookback_minutes=lookback_minutes,
                include_related_data=include_related_data,
                correlation_window_minutes=correlation_window_minutes,
                correlation_limit=correlation_limit,
                anchor_timestamp=anchor_timestamp,
                alertname=alertname,
                labels=labels or {},
                annotations=annotations or {},
                node_name=node_name,
            )
        ).model_dump(mode="json")
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
