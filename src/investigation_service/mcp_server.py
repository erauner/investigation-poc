import os

from mcp.server.fastmcp import FastMCP

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
from .reporting import rank_hypotheses as rank_hypotheses_impl
from .reporting import render_investigation_report as render_investigation_report_impl
from .reporting import resolve_primary_target as resolve_primary_target_impl
from .reporting import submit_evidence_step_artifacts as submit_evidence_step_artifacts_impl
from .reporting import update_investigation_plan as update_investigation_plan_impl
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
    response = normalize_alert_input_impl(
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
    )
    return response.model_dump(mode="json")


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
    response = resolve_primary_target_impl(
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
    )
    return response.model_dump(mode="json")


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
    response = build_investigation_plan_impl(
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
    )
    return response.model_dump(mode="json")


@mcp.tool()
def execute_investigation_step(
    plan: dict,
    incident: dict,
    batch_id: str | None = None,
) -> dict:
    """Execute the remaining pending steps in one bounded evidence batch. This is a lower-level bounded fallback/debug primitive; prefer advance_investigation_runtime for normal orchestration."""
    response = execute_investigation_step_impl(
        ExecuteInvestigationStepRequest(
            plan=plan,
            incident=incident,
            batch_id=batch_id,
        )
    )
    return response.model_dump(mode="json")


@mcp.tool()
def get_active_evidence_batch(plan: dict, incident: dict, batch_id: str | None = None) -> dict:
    """Expose the current bounded evidence batch as an execution-facing contract for the remaining pending steps. Useful for lower-level orchestration or debugging."""
    response = get_active_evidence_batch_impl(
        GetActiveEvidenceBatchRequest(
            plan=plan,
            incident=incident,
            batch_id=batch_id,
        )
    )
    return response.model_dump(mode="json")


@mcp.tool()
def submit_evidence_step_artifacts(
    plan: dict,
    incident: dict,
    submitted_steps: list[dict],
    batch_id: str | None = None,
) -> dict:
    """Submit externally gathered artifacts for externally satisfiable pending steps and reconcile them into the planner-owned control plane. Prefer advance_investigation_runtime when you want the canonical same-batch progress step."""
    response = submit_evidence_step_artifacts_impl(
        SubmitEvidenceArtifactsRequest(
            plan=plan,
            incident=incident,
            batch_id=batch_id,
            submitted_steps=submitted_steps,
        )
    )
    return response.model_dump(mode="json")


@mcp.tool()
def advance_investigation_runtime(
    incident: dict,
    execution_context: dict | None = None,
    submitted_steps: list[dict] | None = None,
    batch_id: str | None = None,
) -> dict:
    """Advance exactly one active evidence batch. This is the preferred runtime-progress surface after build_investigation_plan: reconcile submitted external evidence first, auto-run only remaining same-batch planner-owned steps, and return execution_context for the next advance or final render."""
    response = advance_investigation_runtime_impl(
        AdvanceInvestigationRuntimeRequest(
            incident=incident,
            execution_context=execution_context,
            submitted_steps=submitted_steps or [],
            batch_id=batch_id,
        )
    )
    return response.model_dump(mode="json")


@mcp.tool()
def update_investigation_plan(plan: dict, execution: dict) -> dict:
    """Update plan state after one executed evidence batch. This is a lower-level fallback/debug primitive; prefer advance_investigation_runtime for normal orchestration."""
    response = update_investigation_plan_impl(
        UpdateInvestigationPlanRequest(
            plan=plan,
            execution=execution,
        )
    )
    return response.model_dump(mode="json")


@mcp.tool()
def find_unhealthy_workloads(namespace: str, limit: int = 5, cluster: str | None = None) -> dict:
    """List concrete unhealthy pod targets in a namespace for debugging or exploratory routing inspection. Prefer resolve_primary_target for the planner-led path."""
    response = find_unhealthy_workloads_impl(
        FindUnhealthyWorkloadsRequest(cluster=cluster, namespace=namespace, limit=limit)
    )
    return response.model_dump(mode="json")


@mcp.tool()
def find_unhealthy_pod(namespace: str, cluster: str | None = None) -> dict:
    """Find the single best unhealthy pod candidate in a namespace for debugging or exploratory routing inspection. Prefer resolve_primary_target for the planner-led path."""
    response = find_unhealthy_pod_impl(FindUnhealthyPodRequest(cluster=cluster, namespace=namespace))
    return response.model_dump(mode="json")


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
    response = collect_change_candidates_impl(
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
    )
    return response.model_dump(mode="json")


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
    response = rank_hypotheses_impl(
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
    )
    return response.model_dump(mode="json")


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
    response = render_investigation_report_impl(
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
    )
    return response.model_dump(mode="json")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
