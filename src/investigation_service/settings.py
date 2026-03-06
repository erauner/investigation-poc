import os


def get_prometheus_url() -> str:
    return os.getenv("PROMETHEUS_URL", "http://localhost:9090")


def get_log_tail_lines() -> int:
    raw = os.getenv("LOG_TAIL_LINES", "200")
    try:
        return int(raw)
    except ValueError:
        return 200
