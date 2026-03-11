import os


def get_prometheus_url() -> str:
    return os.getenv("PROMETHEUS_URL", "http://localhost:9090")


def get_kubernetes_mcp_url() -> str:
    return os.getenv("KUBERNETES_MCP_URL", "http://kubernetes-mcp-server.kagent:8080/mcp")


def get_prometheus_mcp_url() -> str:
    return os.getenv("PROMETHEUS_MCP_URL", "http://prometheus-mcp-server.kagent:8080/mcp")


def get_loki_url() -> str | None:
    value = os.getenv("LOKI_URL", "").strip()
    return value or None


def get_loki_mcp_url() -> str | None:
    value = os.getenv("LOKI_MCP_URL", "").strip()
    return value or None


def get_peer_mcp_timeout_seconds() -> float:
    raw = os.getenv("PEER_MCP_TIMEOUT_SECONDS", "10").strip()
    try:
        value = float(raw)
    except ValueError:
        return 10.0
    return min(max(value, 1.0), 60.0)


def get_cluster_registry_path() -> str | None:
    value = os.getenv("CLUSTER_REGISTRY_PATH", "").strip()
    return value or None


def get_kubeconfig_path() -> str | None:
    value = os.getenv("KUBECONFIG_PATH", os.getenv("KUBECONFIG", "")).strip()
    return value or None


def get_default_cluster_alias() -> str | None:
    value = os.getenv("DEFAULT_CLUSTER_ALIAS", "").strip()
    return value or None


def get_log_tail_lines() -> int:
    raw = os.getenv("LOG_TAIL_LINES", "200")
    try:
        return int(raw)
    except ValueError:
        return 200


def get_default_lookback_minutes() -> int:
    raw = os.getenv("DEFAULT_LOOKBACK_MINUTES", "15")
    try:
        value = int(raw)
    except ValueError:
        return 15
    return min(max(value, 1), 240)


def get_allowed_namespaces() -> set[str] | None:
    raw = os.getenv("ALLOWED_NAMESPACES", "").strip()
    if not raw:
        return None
    values = {item.strip() for item in raw.split(",") if item.strip()}
    return values or None


def get_action_mode() -> str:
    raw = os.getenv("ACTION_MODE", "disabled").strip().lower()
    if raw in {"disabled", "proposal-only"}:
        return raw
    return "disabled"


def get_guidelines_enabled() -> bool:
    raw = os.getenv("GUIDELINES_ENABLED", "true").strip().lower()
    return raw not in {"false", "0", "no", "off"}


def get_guidelines_path() -> str:
    return os.getenv("GUIDELINES_PATH", "/etc/investigation-service/guidelines.yaml")


def get_cluster_name() -> str | None:
    value = os.getenv("CLUSTER_NAME", "").strip()
    return value or None
