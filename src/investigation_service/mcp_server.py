import os

from mcp.server.fastmcp import FastMCP

from .models import (
    AlertInvestigationReportRequest,
    CollectAlertContextRequest,
    CollectCorrelatedChangesRequest,
    CollectContextRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    BuildRootCauseReportRequest,
    FindUnhealthyPodRequest,
    FindUnhealthyWorkloadsRequest,
    InvestigationReportRequest,
)
from .correlation import collect_correlated_changes as collect_correlated_changes_impl
from .reporting import build_alert_investigation_report as build_alert_investigation_report_impl
from .reporting import build_investigation_report as build_investigation_report_impl
from .reporting import build_root_cause_report as build_root_cause_report_impl
from .tools import collect_alert_context as collect_alert_context_impl
from .tools import collect_node_context as collect_node_context_impl
from .tools import collect_service_context as collect_service_context_impl
from .tools import collect_workload_context as collect_workload_context_impl
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
def collect_workload_context(
    namespace: str,
    target: str,
    cluster: str | None = None,
    profile: str = "workload",
    service_name: str | None = None,
    lookback_minutes: int = 15,
) -> dict:
    """Collect structured workload context (state, events, logs, metrics, findings) for drill-down after the top-level report."""
    response = collect_workload_context_impl(
        CollectContextRequest(
            cluster=cluster,
            namespace=namespace,
            target=target,
            profile=profile,
            service_name=service_name,
            lookback_minutes=lookback_minutes,
        )
    )
    return response.model_dump(mode="json")


@mcp.tool()
def collect_alert_context(
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
    """Collect structured context for an alert-shaped input by inferring the investigation target. Prefer build_investigation_report first for normal investigations."""
    response = collect_alert_context_impl(
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
def collect_node_context(node_name: str, lookback_minutes: int = 15, cluster: str | None = None) -> dict:
    """Collect structured context for a cluster node target as a lower-level follow-up tool."""
    response = collect_node_context_impl(
        CollectNodeContextRequest(cluster=cluster, node_name=node_name, lookback_minutes=lookback_minutes)
    )
    return response.model_dump(mode="json")


@mcp.tool()
def collect_service_context(
    namespace: str,
    service_name: str,
    cluster: str | None = None,
    target: str | None = None,
    lookback_minutes: int = 15,
) -> dict:
    """Collect structured context for a namespaced service target as a lower-level follow-up tool."""
    response = collect_service_context_impl(
        CollectServiceContextRequest(
            cluster=cluster,
            namespace=namespace,
            service_name=service_name,
            target=target,
            lookback_minutes=lookback_minutes,
        )
    )
    return response.model_dump(mode="json")


@mcp.tool()
def find_unhealthy_workloads(namespace: str, limit: int = 5, cluster: str | None = None) -> dict:
    """List concrete unhealthy pod targets in a namespace for vague workload requests when the user did not name a target."""
    response = find_unhealthy_workloads_impl(
        FindUnhealthyWorkloadsRequest(cluster=cluster, namespace=namespace, limit=limit)
    )
    return response.model_dump(mode="json")


@mcp.tool()
def find_unhealthy_pod(namespace: str, cluster: str | None = None) -> dict:
    """Find the single best unhealthy pod candidate in a namespace for vague workload requests."""
    response = find_unhealthy_pod_impl(FindUnhealthyPodRequest(cluster=cluster, namespace=namespace))
    return response.model_dump(mode="json")


@mcp.tool()
def build_root_cause_report(
    target: str,
    cluster: str | None = None,
    namespace: str | None = None,
    profile: str = "workload",
    service_name: str | None = None,
    lookback_minutes: int = 15,
) -> dict:
    """Collect context for a normalized target and return a typed root-cause report. Prefer build_investigation_report for the default user-facing flow."""
    response = build_root_cause_report_impl(
        BuildRootCauseReportRequest(
            cluster=cluster,
            namespace=namespace,
            target=target,
            profile=profile,
            service_name=service_name,
            lookback_minutes=lookback_minutes,
        )
    )
    return response.model_dump(mode="json")


@mcp.tool()
def build_investigation_report(
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
    """Build the final typed investigation report for normal investigations. Use this first when namespace and target are already known, including Backend/<name>, Frontend/<name>, and Cluster/<name> convenience targets, because backend routing resolves them to the correct deployment or service target automatically."""
    response = build_investigation_report_impl(
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


@mcp.tool()
def build_alert_investigation_report(
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
    include_related_data: bool = True,
    correlation_window_minutes: int = 60,
    correlation_limit: int = 10,
    anchor_timestamp: str | None = None,
) -> dict:
    """Build the final typed investigation report for alert-shaped input. Prefer this explicit alert triage entrypoint over the generic report tool when alertname, labels, or annotations are the primary input."""
    response = build_alert_investigation_report_impl(
        AlertInvestigationReportRequest(
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
            include_related_data=include_related_data,
            correlation_window_minutes=correlation_window_minutes,
            correlation_limit=correlation_limit,
            anchor_timestamp=anchor_timestamp,
        )
    )
    return response.model_dump(mode="json")


@mcp.tool()
def collect_correlated_changes(
    target: str,
    cluster: str | None = None,
    namespace: str | None = None,
    profile: str = "workload",
    service_name: str | None = None,
    lookback_minutes: int = 60,
    anchor_timestamp: str | None = None,
    limit: int = 10,
) -> dict:
    """Collect bounded, ranked correlated changes for a normalized target as follow-up after the top-level report when deeper inspection is needed."""
    response = collect_correlated_changes_impl(
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


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
