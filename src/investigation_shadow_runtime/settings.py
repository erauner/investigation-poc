import os


def get_shadow_runtime_host() -> str:
    return os.getenv("HOST", "0.0.0.0")


def get_shadow_runtime_port() -> int:
    raw = os.getenv("PORT", "8080").strip()
    try:
        return int(raw)
    except ValueError:
        return 8080


def get_shadow_agent_name() -> str:
    value = os.getenv("SHADOW_AGENT_NAME", "incident-triage-shadow").strip()
    return value or "incident-triage-shadow"


def get_shadow_checkpoint_mode() -> str:
    value = os.getenv("SHADOW_CHECKPOINT_MODE", "kagent").strip().lower()
    if value in {"memory", "kagent"}:
        return value
    return "kagent"
