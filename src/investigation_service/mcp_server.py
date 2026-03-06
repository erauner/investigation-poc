import os

from mcp.server.fastmcp import FastMCP

from .models import CollectAlertContextRequest, CollectContextRequest
from .tools import collect_alert_context as collect_alert_context_impl
from .tools import collect_workload_context as collect_workload_context_impl

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
    profile: str = "workload",
    service_name: str | None = None,
    lookback_minutes: int = 15,
) -> dict:
    """Collect structured workload context (state, events, logs, metrics, findings)."""
    response = collect_workload_context_impl(
        CollectContextRequest(
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
    namespace: str | None = None,
    node_name: str | None = None,
    target: str | None = None,
    profile: str = "workload",
    service_name: str | None = None,
    lookback_minutes: int = 15,
) -> dict:
    """Collect structured context for an alert-shaped input by inferring the investigation target."""
    response = collect_alert_context_impl(
        CollectAlertContextRequest(
            alertname=alertname,
            labels=labels or {},
            annotations=annotations or {},
            namespace=namespace,
            node_name=node_name,
            target=target,
            profile=profile,
            service_name=service_name,
            lookback_minutes=lookback_minutes,
        )
    )
    return response.model_dump(mode="json")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
