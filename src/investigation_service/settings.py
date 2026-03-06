import os


def get_prometheus_url() -> str:
    return os.getenv("PROMETHEUS_URL", "http://localhost:9090")


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
