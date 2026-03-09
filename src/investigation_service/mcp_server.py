import os

from mcp.server.fastmcp import FastMCP

from .models import (
    BuildInvestigationPlanRequest,
    CollectAlertContextRequest,
    CollectCorrelatedChangesRequest,
    CollectContextRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    ExecuteInvestigationStepRequest,
    FindUnhealthyPodRequest,
    FindUnhealthyWorkloadsRequest,
    InvestigationReportRequest,
    UpdateInvestigationPlanRequest,
)
from .correlation import collect_change_candidates as collect_change_candidates_impl
from .reporting import build_investigation_plan as build_investigation_plan_impl
from .reporting import execute_investigation_step as execute_investigation_step_impl
from .reporting import rank_hypotheses as rank_hypotheses_impl
from .reporting import render_investigation_report as render_investigation_report_impl
from .reporting import resolve_primary_target as resolve_primary_target_impl
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
    """Build an explicit investigation plan without collecting evidence. Use this to start planner-led investigations before gathering evidence by plane."""
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
    """Execute one bounded evidence batch from an investigation plan. This is a control-plane step that dispatches only planner-owned evidence batches."""
    response = execute_investigation_step_impl(
        ExecuteInvestigationStepRequest(
            plan=plan,
            incident=incident,
            batch_id=batch_id,
        )
    )
    return response.model_dump(mode="json")


@mcp.tool()
def update_investigation_plan(plan: dict, execution: dict) -> dict:
    """Update plan state after one executed evidence batch. Use this before ranking hypotheses or rendering a report."""
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
) -> dict:
    """Analyze collected investigation evidence and return ranked hypotheses without rendering the final report."""
    response = rank_hypotheses_impl(
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
) -> dict:
    """Render the final investigation report from the staged artifact-oriented pipeline."""
    response = render_investigation_report_impl(
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
    )
    return response.model_dump(mode="json")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
