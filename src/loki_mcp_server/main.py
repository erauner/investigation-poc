import os
from datetime import datetime, timezone
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP


def _normalize_time(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _parse_json_response(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise ValueError(f"Loki API returned non-success payload: {payload}")
    return payload


def _format_query_response(payload: dict[str, Any], format: str) -> Any:
    if format == "json":
        return payload
    results = payload.get("data", {}).get("result", [])
    lines: list[str] = []
    for stream in results:
        labels = ",".join(f"{key}={value}" for key, value in sorted((stream.get("stream") or {}).items()))
        for entry in stream.get("values") or []:
            if len(entry) < 2:
                continue
            timestamp_ns, line = entry[0], entry[1]
            timestamp = datetime.fromtimestamp(int(timestamp_ns) / 1_000_000_000, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            lines.append(f"{timestamp} {{{labels}}} {line}")
    if format == "text":
        return "\n".join(lines)
    return {"entries": lines}


def _params(
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if query is not None:
        params["query"] = query
    normalized_start = _normalize_time(start)
    normalized_end = _normalize_time(end)
    if normalized_start:
        params["start"] = normalized_start
    if normalized_end:
        params["end"] = normalized_end
    if limit is not None:
        params["limit"] = limit
    return params


def _headers(token: str | None = None, org: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org:
        headers["X-Scope-OrgID"] = org
    return headers


mcp = FastMCP(
    "loki-mcp-server",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("PORT", os.getenv("MCP_PORT", "8080"))),
    streamable_http_path=os.getenv("MCP_PATH", "/stream"),
)


@mcp.tool()
def loki_query(
    query: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
    url: str | None = None,
    token: str | None = None,
    org: str | None = None,
    format: str = "raw",
) -> Any:
    """Run a LogQL query against Loki."""
    loki_url = (url or os.getenv("LOKI_URL", "")).strip()
    if not loki_url:
        raise ValueError("LOKI_URL is required")
    with httpx.Client(timeout=httpx.Timeout(20)) as client:
        payload = _parse_json_response(
            client.get(
                f"{loki_url.rstrip('/')}/loki/api/v1/query_range",
                params=_params(query=query, start=start, end=end, limit=limit),
                headers=_headers(token=token or os.getenv("LOKI_TOKEN"), org=org or os.getenv("LOKI_ORG_ID")),
            )
        )
    return _format_query_response(payload, format)


@mcp.tool()
def loki_label_names(
    start: str | None = None,
    end: str | None = None,
    url: str | None = None,
    token: str | None = None,
    org: str | None = None,
    format: str = "raw",
) -> Any:
    """Get all label names from Loki."""
    loki_url = (url or os.getenv("LOKI_URL", "")).strip()
    if not loki_url:
        raise ValueError("LOKI_URL is required")
    with httpx.Client(timeout=httpx.Timeout(20)) as client:
        payload = _parse_json_response(
            client.get(
                f"{loki_url.rstrip('/')}/loki/api/v1/labels",
                params=_params(start=start, end=end),
                headers=_headers(token=token or os.getenv("LOKI_TOKEN"), org=org or os.getenv("LOKI_ORG_ID")),
            )
        )
    if format == "json":
        return payload
    data = payload.get("data") or []
    if format == "text":
        return "\n".join(str(item) for item in data)
    return {"labels": data}


@mcp.tool()
def loki_label_values(
    label: str,
    start: str | None = None,
    end: str | None = None,
    url: str | None = None,
    token: str | None = None,
    org: str | None = None,
    format: str = "raw",
) -> Any:
    """Get all values for a Loki label."""
    loki_url = (url or os.getenv("LOKI_URL", "")).strip()
    if not loki_url:
        raise ValueError("LOKI_URL is required")
    with httpx.Client(timeout=httpx.Timeout(20)) as client:
        payload = _parse_json_response(
            client.get(
                f"{loki_url.rstrip('/')}/loki/api/v1/label/{label}/values",
                params=_params(start=start, end=end),
                headers=_headers(token=token or os.getenv("LOKI_TOKEN"), org=org or os.getenv("LOKI_ORG_ID")),
            )
        )
    if format == "json":
        return payload
    data = payload.get("data") or []
    if format == "text":
        return "\n".join(str(item) for item in data)
    return {"values": data}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
