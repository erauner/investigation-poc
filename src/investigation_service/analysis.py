from .models import Finding


def _derive_workload_findings(object_state: dict, events: list[str], logs: str, metrics: dict) -> list[Finding]:
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

    restart_rate = metrics.get("pod_restart_rate")
    if restart_rate is not None and restart_rate > 0:
        findings.append(
            Finding(
                severity="warning",
                source="prometheus",
                title="Pod Restarts Increasing",
                evidence=f"Restart rate over lookback window: {restart_rate:.4f}/s",
            )
        )

    return findings


def _derive_service_findings(metrics: dict) -> list[Finding]:
    findings: list[Finding] = []

    error_rate = metrics.get("service_error_rate")
    if error_rate is not None and error_rate > 0:
        findings.append(
            Finding(
                severity="warning",
                source="prometheus",
                title="Service Returning 5xx Responses",
                evidence=f"5xx request rate over lookback window: {error_rate:.4f}/s",
            )
        )

    p95_latency = metrics.get("service_latency_p95_seconds")
    if p95_latency is not None and p95_latency > 1.0:
        findings.append(
            Finding(
                severity="warning",
                source="prometheus",
                title="High Service Latency",
                evidence=f"p95 latency is {p95_latency:.3f}s",
            )
        )

    return findings


def _derive_pipeline_findings(metrics: dict) -> list[Finding]:
    findings: list[Finding] = []
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
    return findings


def derive_findings(profile: str, object_state: dict, events: list[str], logs: str, metrics: dict) -> list[Finding]:
    findings: list[Finding] = []

    if profile == "service":
        findings.extend(_derive_service_findings(metrics))
    elif profile == "otel-pipeline":
        findings.extend(_derive_pipeline_findings(metrics))
    else:
        findings.extend(_derive_workload_findings(object_state, events, logs, metrics))
        findings.extend(_derive_pipeline_findings(metrics))

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
