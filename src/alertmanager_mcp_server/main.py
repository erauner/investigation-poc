import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    token = os.getenv("ALERTMANAGER_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_alerts_response(response: httpx.Response) -> list[dict[str, Any]]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"Alertmanager API returned malformed payload: {payload}")
    return payload


def _normalize_alerts(raw_alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in raw_alerts:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "fingerprint": item.get("fingerprint"),
                "status": item.get("status") or {},
                "labels": item.get("labels") or {},
                "annotations": item.get("annotations") or {},
                "startsAt": item.get("startsAt"),
                "endsAt": item.get("endsAt"),
                "updatedAt": item.get("updatedAt"),
                "generatorURL": item.get("generatorURL"),
            }
        )
    return normalized


mcp = FastMCP(
    "alertmanager-mcp-server",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("PORT", os.getenv("MCP_PORT", "8080"))),
    streamable_http_path=os.getenv("MCP_PATH", "/stream"),
)


@mcp.tool()
def alertmanager_list_alerts(
    labelFilters: dict[str, str] | None = None,
    active: bool = True,
    silenced: bool = False,
    inhibited: bool = False,
    unprocessed: bool = False,
) -> dict[str, Any]:
    """List matching alerts from Alertmanager."""
    alertmanager_url = os.getenv("ALERTMANAGER_URL", "").strip()
    if not alertmanager_url:
        raise ValueError("ALERTMANAGER_URL is required")
    params: list[tuple[str, str]] = []
    for key, value in (labelFilters or {}).items():
        if value:
            params.append(("filter", f'{key}="{value}"'))
    if active:
        params.append(("active", "true"))
    if silenced:
        params.append(("silenced", "true"))
    if inhibited:
        params.append(("inhibited", "true"))
    if unprocessed:
        params.append(("unprocessed", "true"))
    with httpx.Client(timeout=httpx.Timeout(20)) as client:
        alerts = _parse_alerts_response(
            client.get(
                f"{alertmanager_url.rstrip('/')}/api/v2/alerts",
                params=params,
                headers=_headers(),
            )
        )
    return {"alerts": _normalize_alerts(alerts)}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
