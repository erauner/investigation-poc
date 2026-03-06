from .models import Finding


def derive_findings(object_state: dict, events: list[str], logs: str, metrics: dict) -> list[Finding]:
    findings: list[Finding] = []

    if object_state.get("error"):
        findings.append(
            Finding(
                severity="critical",
                source="k8s",
                title="Target Not Found",
                evidence=str(object_state.get("error")),
            )
        )

    event_blob = "\n".join(events).lower()
    if "crashloopbackoff" in event_blob or "backoff" in event_blob:
        findings.append(
            Finding(
                severity="critical",
                source="events",
                title="Crash Loop Detected",
                evidence="Events indicate BackOff/CrashLoopBackOff behavior",
            )
        )

    if "oomkilled" in event_blob or "oom" in logs.lower():
        findings.append(
            Finding(
                severity="critical",
                source="heuristic",
                title="Possible OOM Condition",
                evidence="Found OOM signal in events or logs",
            )
        )

    if "error" in logs.lower() or "exception" in logs.lower():
        findings.append(
            Finding(
                severity="warning",
                source="logs",
                title="Error-like Log Patterns",
                evidence="Recent logs contain 'error' or 'exception'",
            )
        )

    spans = metrics.get("accepted_spans_per_sec")
    if spans is None or spans <= 0:
        findings.append(
            Finding(
                severity="info",
                source="prometheus",
                title="No Active Span Ingestion",
                evidence="Prometheus shows no recent accepted spans",
            )
        )

    if not findings:
        findings.append(
            Finding(
                severity="info",
                source="heuristic",
                title="No Critical Signals Found",
                evidence="No obvious failure signature detected from current inputs",
            )
        )

    return findings
