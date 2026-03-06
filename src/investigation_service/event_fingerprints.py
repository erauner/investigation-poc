import re


def normalize_event_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def fingerprint_event(
    *,
    resource_kind: str | None,
    namespace: str | None,
    name: str | None,
    reason: str,
    message: str,
) -> str:
    return (
        f"event|{(resource_kind or 'event').lower()}|"
        f"{namespace or 'cluster'}|{name or 'unknown'}|"
        f"{normalize_event_text(reason)}|{normalize_event_text(message or reason)}"
    )


def parse_compact_event_text(event_text: str) -> tuple[str, str]:
    parts = event_text.strip().split(None, 2)
    if len(parts) >= 3:
        return parts[1].rstrip(":"), parts[2]
    if len(parts) == 2:
        return parts[1].rstrip(":"), parts[1]
    if len(parts) == 1:
        return parts[0].rstrip(":"), parts[0]
    return "Event", "Event"
