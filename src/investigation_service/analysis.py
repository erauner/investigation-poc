from .models import Finding


def _derive_node_findings(object_state: dict, metrics: dict) -> list[Finding]:
    findings: list[Finding] = []
    conditions = {
        item.get("type"): item.get("status")
        for item in object_state.get("conditions", [])
        if item.get("type")
    }
    if conditions.get("Ready") == "False":
        findings.append(
            Finding(
                severity="critical",
                source="k8s",
                title="Node Not Ready",
                evidence="Node condition Ready=False",
            )
        )
    if conditions.get("MemoryPressure") == "True":
        findings.append(
            Finding(
                severity="warning",
                source="k8s",
                title="Node Memory Pressure",
                evidence="Node condition MemoryPressure=True",
            )
        )

    request_bytes = metrics.get("node_memory_request_bytes")
    allocatable_bytes = metrics.get("node_memory_allocatable_bytes")
    working_set_bytes = metrics.get("node_memory_working_set_bytes")
    if request_bytes is not None and allocatable_bytes and allocatable_bytes > 0:
        request_utilization = request_bytes / allocatable_bytes
        working_set_utilization = (
            working_set_bytes / allocatable_bytes
            if working_set_bytes is not None and allocatable_bytes > 0
            else None
        )
        if request_utilization >= 0.85:
            evidence = f"Memory requests are at {request_utilization:.1%} of allocatable capacity"
            if working_set_utilization is not None and working_set_utilization < 0.85:
                evidence += (
                    f", while observed working set is {working_set_utilization:.1%}; "
                    "this indicates request saturation more than active node memory pressure"
                )
            findings.append(
                Finding(
                    severity="warning",
                    source="prometheus",
                    title="High Node Memory Request Saturation",
                    evidence=evidence,
                )
            )
    return findings


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

    if object_state.get("kind") == "node":
        findings.extend(_derive_node_findings(object_state, metrics))

    containers = object_state.get("containers", [])
    for container in containers:
        exit_code = container.get("lastTerminationExitCode")
        termination_reason = container.get("lastTerminationReason")
        waiting_reason = container.get("waitingReason")
        command = container.get("command", [])
        args = container.get("args", [])
        if exit_code is not None or termination_reason or waiting_reason:
            command_text = " ".join([*command, *args]).strip()
            evidence_parts = []
            if waiting_reason:
                evidence_parts.append(f"waiting reason={waiting_reason}")
            if termination_reason:
                evidence_parts.append(f"last termination reason={termination_reason}")
            if exit_code is not None:
                evidence_parts.append(f"exit code={exit_code}")
            if command_text:
                evidence_parts.append(f"command='{command_text}'")
            findings.append(
                Finding(
                    severity="critical" if waiting_reason == "CrashLoopBackOff" else "warning",
                    source="k8s",
                    title="Container Restart Failure Details",
                    evidence=", ".join(evidence_parts),
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

    if object_state.get("kind") == "node":
        findings.extend(_derive_node_findings(object_state, metrics))
    elif profile == "service":
        findings.extend(_derive_service_findings(metrics))
    elif profile == "otel-pipeline":
        findings.extend(_derive_pipeline_findings(metrics))
    else:
        findings.extend(_derive_workload_findings(object_state, events, logs, metrics))

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
