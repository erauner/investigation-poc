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
    if len(parts) >= 3 and parts[0] in {"Normal", "Warning"}:
        return parts[1].rstrip(":"), parts[2]
    if len(parts) >= 2:
        return parts[0].rstrip(":"), " ".join(parts[1:])
    if len(parts) == 2:
        return parts[1].rstrip(":"), parts[1]
    if len(parts) == 1:
        return parts[0].rstrip(":"), parts[0]
    return "Event", "Event"


def canonicalize_event_fingerprint(value: str) -> str:
    parts = value.split("|")
    if not parts or parts[0] != "event":
        return value

    if len(parts) == 4 and "/" in parts[1]:
        resource_kind, name = parts[1].split("/", 1)
        return fingerprint_event(
            resource_kind=resource_kind,
            namespace=None,
            name=name,
            reason=parts[2],
            message=parts[3],
        )

    if len(parts) != 6:
        return value

    resource_kind = parts[1]
    namespace = parts[2]
    name = parts[3]
    reason = parts[4]
    message = parts[5]

    if "/" in resource_kind:
        kind, parsed_name = resource_kind.split("/", 1)
        return fingerprint_event(
            resource_kind=kind,
            namespace=namespace if namespace and namespace != "cluster" else None,
            name=parsed_name,
            reason=reason,
            message=message,
        )

    return fingerprint_event(
        resource_kind=resource_kind,
        namespace=namespace if namespace else None,
        name=name if name else None,
        reason=reason,
        message=message,
    )
