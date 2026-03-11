import re

from .event_fingerprints import fingerprint_event, parse_compact_event_text
from .models import (
    ConfidenceType,
    EvidenceBundle,
    EvidenceItem,
    Finding,
    Hypothesis,
    InvestigationAnalysis,
    InvestigationTarget,
)

_SOURCE_PRIORITY = {
    "k8s": 50,
    "events": 40,
    "prometheus": 30,
    "logs": 20,
    "heuristic": 10,
}

_SCOPE_TITLE_PRIORITY = {
    "workload": {
        "Init Container Dependency Blocked": 90,
        "Container Restart Failure Details": 80,
        "Crash Loop Detected": 70,
        "Service Returning 5xx Responses": 68,
        "High Service Latency": 62,
        "Possible OOM Condition": 65,
        "Target Not Found": 60,
        "Error-like Log Patterns": 5,
        "Pod Restarts Increasing": 15,
        "No Critical Signals Found": 0,
    },
    "service": {
        "Service Has No Matching Backends": 80,
        "Service Has No Ready Backends": 75,
        "Service Backends Restarting": 65,
        "Service Returning 5xx Responses": 70,
        "High Service Latency": 60,
        "Target Not Found": 55,
        "No Critical Signals Found": 0,
    },
    "node": {
        "Node Not Ready": 80,
        "Node Memory Pressure": 70,
        "High Node Memory Request Saturation": 65,
        "Target Not Found": 55,
        "No Critical Signals Found": 0,
    },
    "otel-pipeline": {
        "No Active Span Ingestion": 70,
        "Target Not Found": 55,
        "No Critical Signals Found": 0,
    },
}

_SEVERITY_PRIORITY = {
    "critical": 300,
    "warning": 200,
    "info": 100,
}


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


def _derive_service_findings(object_state: dict, metrics: dict) -> list[Finding]:
    findings: list[Finding] = []

    matched_pod_count = object_state.get("matchedPodCount")
    ready_pod_count = object_state.get("readyPodCount")
    matched_pods = object_state.get("matchedPods") or []
    matched_workloads = object_state.get("matchedWorkloads") or []
    selector = object_state.get("selector") or {}
    if selector and matched_pod_count == 0:
        findings.append(
            Finding(
                severity="critical",
                source="k8s",
                title="Service Has No Matching Backends",
                evidence=f"Selector {selector} did not match any live pods",
            )
        )
    elif matched_pod_count and ready_pod_count == 0:
        workload_note = ""
        if matched_workloads:
            workload_names = ", ".join(f"{item.get('kind')}/{item.get('name')}" for item in matched_workloads)
            workload_note = f" for workloads {workload_names}"
        findings.append(
            Finding(
                severity="critical",
                source="k8s",
                title="Service Has No Ready Backends",
                evidence=f"Matched {matched_pod_count} backend pods but 0 are ready{workload_note}",
            )
        )
    restarting_pods = [pod for pod in matched_pods if (pod.get("restartCount") or 0) > 0]
    if restarting_pods:
        summaries = ", ".join(
            f"{pod.get('name')} restarts={pod.get('restartCount')}"
            for pod in restarting_pods[:3]
            if pod.get("name")
        )
        findings.append(
            Finding(
                severity="warning",
                source="k8s",
                title="Service Backends Restarting",
                evidence=summaries or "One or more service backend pods are restarting",
            )
        )

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


def _derive_error_like_log_findings(
    logs: str,
    *,
    service_degradation_present: bool,
    require_corroborating_degradation: bool = False,
) -> list[Finding]:
    if "error" not in logs.lower() and "exception" not in logs.lower():
        return []
    if require_corroborating_degradation and not service_degradation_present:
        return []
    return [
        Finding(
            severity="info" if service_degradation_present else "warning",
            source="logs",
            title="Error-like Log Patterns",
            evidence="Recent logs contain 'error' or 'exception'",
        )
    ]


def _derive_workload_findings(object_state: dict, events: list[str], logs: str, metrics: dict) -> list[Finding]:
    findings: list[Finding] = []
    service_error_rate = metrics.get("service_error_rate")
    service_latency_p95 = metrics.get("service_latency_p95_seconds")
    service_degradation_present = bool(
        (service_error_rate is not None and service_error_rate > 0)
        or (service_latency_p95 is not None and service_latency_p95 > 1.0)
    )

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

    runtime_state = object_state
    if object_state.get("kind") in {"deployment", "statefulset"} and object_state.get("runtimePod"):
        runtime_state = object_state["runtimePod"]

    init_containers = runtime_state.get("initContainers", [])
    for container in init_containers:
        waiting_reason = container.get("waitingReason")
        termination_reason = container.get("terminationReason") or container.get("lastTerminationReason")
        exit_code = container.get("terminationExitCode")
        if exit_code is None:
            exit_code = container.get("lastTerminationExitCode")
        restart_count = container.get("restartCount", 0)
        command = container.get("command", [])
        args = container.get("args", [])
        if not (waiting_reason or termination_reason or exit_code is not None or restart_count > 0):
            continue

        command_text = " ".join([*command, *args]).strip()
        evidence_parts = [f"init container={container.get('name')}"]
        if waiting_reason:
            evidence_parts.append(f"waiting reason={waiting_reason}")
        if termination_reason:
            evidence_parts.append(f"termination reason={termination_reason}")
        if exit_code is not None:
            evidence_parts.append(f"exit code={exit_code}")
        if restart_count:
            evidence_parts.append(f"restarts={restart_count}")
        if command_text:
            evidence_parts.append(f"command='{command_text}'")
        if logs:
            lower_logs = logs.lower()
            dependency_markers = ("waiting for", "connection refused", "dial tcp", "timed out", "could not connect")
            if any(marker in lower_logs for marker in dependency_markers):
                evidence_parts.append("init logs indicate dependency wait or connection failure")

        findings.append(
            Finding(
                severity="critical",
                source="k8s",
                title="Init Container Dependency Blocked",
                evidence=", ".join(evidence_parts),
            )
        )
        break

    containers = runtime_state.get("containers", [])
    for container in containers:
        exit_code = container.get("lastTerminationExitCode")
        termination_reason = container.get("lastTerminationReason")
        waiting_reason = container.get("waitingReason")
        command = container.get("command", [])
        args = container.get("args", [])
        if waiting_reason == "PodInitializing":
            continue
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

    findings.extend(
        _derive_error_like_log_findings(
            logs,
            service_degradation_present=service_degradation_present,
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

    findings.extend(_derive_service_findings(object_state, metrics))

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
        findings.extend(_derive_service_findings(object_state, metrics))
        service_degradation_present = bool(
            (metrics.get("service_error_rate") is not None and metrics.get("service_error_rate") > 0)
            or (metrics.get("service_latency_p95_seconds") is not None and metrics.get("service_latency_p95_seconds") > 1.0)
        )
        findings.extend(
            _derive_error_like_log_findings(
                logs,
                service_degradation_present=service_degradation_present,
                require_corroborating_degradation=True,
            )
        )
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


def _target_requested_kind(target: InvestigationTarget) -> str | None:
    requested = target.requested_target
    if "/" not in requested:
        return None
    return requested.split("/", 1)[0].strip().lower() or None


def _finding_score(scope: str, finding: Finding) -> int:
    return (
        _SEVERITY_PRIORITY.get(finding.severity, 0)
        + _SOURCE_PRIORITY.get(finding.source, 0)
        + _SCOPE_TITLE_PRIORITY.get(scope, {}).get(finding.title, 0)
    )


def _ranked_findings(bundle: EvidenceBundle, scope: str) -> list[Finding]:
    return sorted(bundle.findings, key=lambda item: _finding_score(scope, item), reverse=True)


def _select_confidence(scope: str, lead: Finding, limitations: list[str]) -> ConfidenceType:
    limitation_penalty = 0
    if limitations:
        limitation_penalty += 1
    if any("metrics unavailable" in item or "metric unavailable" in item for item in limitations):
        limitation_penalty += 1
    if any("query failed" in item or ("target" in item and "failed" in item) for item in limitations):
        limitation_penalty += 1

    base_score = 0
    if lead.title in {"Init Container Dependency Blocked", "Container Restart Failure Details", "Node Not Ready", "Service Returning 5xx Responses"}:
        base_score = 3
    elif lead.title in {"Crash Loop Detected", "High Node Memory Request Saturation", "High Service Latency"}:
        base_score = 2
    elif lead.severity == "critical":
        base_score = 2
    elif lead.severity == "warning":
        base_score = 1

    if scope == "workload" and lead.title == "Container Restart Failure Details":
        base_score = 3

    adjusted = max(0, base_score - limitation_penalty)
    if adjusted >= 3:
        return "high"
    if adjusted >= 1:
        return "medium"
    return "low"


def _extract_field(pattern: str, evidence: str) -> str | None:
    match = re.search(pattern, evidence)
    if match:
        return match.group(1)
    return None


def _derive_likely_cause(scope: str, lead: Finding, requested_target_kind: str | None) -> str | None:
    if lead.source == "heuristic" and lead.title == "No Critical Signals Found":
        return None
    if lead.title == "Target Not Found":
        return "The requested target could not be resolved from the current cluster state."
    if lead.title == "Container Restart Failure Details":
        waiting_reason = _extract_field(r"waiting reason=([^,]+)", lead.evidence)
        exit_code = _extract_field(r"exit code=([0-9]+)", lead.evidence)
        command = _extract_field(r"command='([^']+)'", lead.evidence)
        if waiting_reason == "CrashLoopBackOff" and exit_code and command:
            return f"Container command '{command}' is exiting with code {exit_code}, driving repeated CrashLoopBackOff restarts."
        if waiting_reason == "CrashLoopBackOff" and exit_code:
            return f"Container is exiting with code {exit_code}, which is causing repeated CrashLoopBackOff restarts."
        if waiting_reason:
            return f"Container is repeatedly entering {waiting_reason} based on direct pod status."
    if lead.title == "Init Container Dependency Blocked":
        container_name = _extract_field(r"init container=([^,]+)", lead.evidence)
        waiting_reason = _extract_field(r"waiting reason=([^,]+)", lead.evidence)
        if container_name and waiting_reason:
            return (
                f"Init container '{container_name}' is blocked in {waiting_reason}, preventing the workload from completing startup."
            )
        if container_name:
            return f"Init container '{container_name}' is blocking workload startup before the main container can run."
        return "An init container is blocking workload startup before the main container can run."
    if lead.title == "Crash Loop Detected":
        if requested_target_kind == "pod":
            return "The pod is repeatedly failing shortly after start, so Kubernetes is backing off restarts."
        return "Kubernetes is backing off container restarts because the workload keeps failing shortly after start."
    if lead.title == "Possible OOM Condition":
        return "The workload likely exhausted its memory limit or node memory, causing OOM termination signals."
    if lead.title == "Node Not Ready":
        return "The node Ready condition is false, so workloads on that node may be degraded or unschedulable."
    if lead.title == "High Node Memory Request Saturation":
        return (
            "Scheduled workloads have requested most allocatable node memory; this suggests low headroom and may reflect request saturation "
            "more than active pressure."
        )
    if lead.title == "Node Memory Pressure":
        return "The node is reporting active memory pressure through its Kubernetes condition."
    if lead.title == "Service Returning 5xx Responses":
        return "The service is currently returning server errors, indicating backend or dependency failures under live traffic."
    if lead.title == "High Service Latency":
        return "The service is responding slowly, which suggests downstream dependency latency or overloaded request handling."
    if scope == "otel-pipeline" and lead.title == "No Active Span Ingestion":
        return "Telemetry ingestion appears idle or broken because accepted span throughput is absent."
    if lead.source == "heuristic":
        return None
    return lead.title


def _evidence_items_for_hypothesis(bundle: EvidenceBundle, scope: str, findings: list[Finding]) -> list[EvidenceItem]:
    evidence_items = [
        EvidenceItem(
            fingerprint=f"finding|{scope}|{re.sub(r'\\s+', ' ', item.title.strip().lower())}|{re.sub(r'\\s+', ' ', item.evidence.strip().lower())}",
            source=item.source,
            kind="finding",
            severity=item.severity,
            summary=f"{item.source}: {item.title}",
            detail=item.evidence,
        )
        for item in findings[:5]
    ]
    service_request_rate = bundle.metrics.get("service_request_rate")
    if service_request_rate is not None and service_request_rate > 0:
        request_rate_item = EvidenceItem(
            fingerprint=f"metric|{scope}|service_request_rate|{service_request_rate:.4f}",
            source="prometheus",
            kind="metric",
            severity="info",
            summary="prometheus: Service Request Rate",
            detail=f"request rate over lookback window: {service_request_rate:.4f}/s",
        )
        if request_rate_item.fingerprint not in {item.fingerprint for item in evidence_items}:
            evidence_items.append(request_rate_item)
    if bundle.events and bundle.events != ["no related events"]:
        first_event = bundle.events[0]
        reason, message = parse_compact_event_text(first_event)
        event_item = EvidenceItem(
            fingerprint=fingerprint_event(
                resource_kind=bundle.target.kind,
                namespace=bundle.target.namespace,
                name=bundle.target.name,
                reason=reason,
                message=message,
            ),
            source="events",
            kind="event",
            severity="warning",
            summary="recent events",
            detail=first_event,
        )
        if event_item.fingerprint not in {item.fingerprint for item in evidence_items}:
            evidence_items.append(event_item)
    if scope == "node" and bundle.object_state.get("top_pods_by_memory_request"):
        top_pods = bundle.object_state["top_pods_by_memory_request"][:3]
        pod_details = ", ".join(
            f"{item.get('namespace')}/{item.get('name')} ({int(item.get('memory_request_bytes', 0))}B req)"
            for item in top_pods
        )
        top_pods_item = EvidenceItem(
            fingerprint=f"object_state|node|top_pods|{pod_details}",
            source="k8s",
            kind="object_state",
            severity="info",
            summary="k8s: Top Node Memory Request Consumers",
            detail=pod_details,
        )
        if top_pods_item.fingerprint not in {item.fingerprint for item in evidence_items}:
            evidence_items.append(top_pods_item)
    return evidence_items


def build_primary_evidence_from_bundle(bundle: EvidenceBundle, scope: str) -> list[EvidenceItem]:
    ranked = _ranked_findings(bundle, scope)
    return _evidence_items_for_hypothesis(bundle, scope, ranked[:5])


def _operator_target_follow_up_from_target(target: InvestigationTarget) -> str | None:
    for note in target.normalization_notes:
        match = re.match(
            r"resolved\s+((?:Backend|Frontend|Cluster)/[A-Za-z0-9][A-Za-z0-9\-\.]*)\s+to\s+(.+)",
            note,
        )
        if not match:
            continue
        source_target = match.group(1)
        resolved_target = match.group(2)
        return (
            f"This investigation was requested via {source_target}, which resolved to {resolved_target}. "
            "Prefer checking operator reconciliation and updating the owning resource rather than editing pods directly."
        )
    return None


def _follow_ups_for_analysis(bundle: EvidenceBundle, scope: str, target: InvestigationTarget) -> list[str]:
    follow_ups = list(bundle.enrichment_hints)
    if any("logs unavailable" in item for item in bundle.limitations):
        follow_ups.append("Fetch full pod logs or a previous container log stream to confirm the failure path.")
    if any("metrics unavailable" in item or "metric unavailable" in item for item in bundle.limitations):
        follow_ups.append("Use observability tooling for metrics, traces, or dashboards before making a change.")
    if scope == "service":
        follow_ups.append("Check whether a recent rollout or upstream dependency change lines up with the service degradation.")
    if scope == "node":
        follow_ups.append("Review top memory consumers and recent scheduling pressure on the affected node.")
    operator_target_follow_up = _operator_target_follow_up_from_target(target)
    if operator_target_follow_up:
        follow_ups.append(operator_target_follow_up)
    return sorted(set(follow_ups))


def _recommended_next_step(scope: str, profile: str) -> str:
    if scope == "service":
        return "Inspect service dashboards, recent deploys, and upstream or downstream dependencies before changing traffic handling."
    if scope == "node":
        return "Inspect allocatable vs requests, top consumers, and recent node condition changes before taking capacity actions."
    if profile == "otel-pipeline":
        return "Verify collector ingestion, exporter health, and recent telemetry pipeline changes before restarting components."
    return "Confirm the failure with describe output, recent logs, and rollout history before taking write actions."


def _group_findings_into_hypotheses(bundle: EvidenceBundle, scope: str, target: InvestigationTarget) -> list[Hypothesis]:
    ranked = _ranked_findings(bundle, scope)
    if not ranked:
        ranked = [
            Finding(
                severity="info",
                source="heuristic",
                title="No Critical Signals Found",
                evidence="No obvious failure signature detected from current inputs",
            )
        ]
    requested_target_kind = _target_requested_kind(target)
    hypotheses: list[Hypothesis] = []
    for finding in ranked[:3]:
        score = _finding_score(scope, finding)
        hypotheses.append(
            Hypothesis(
                key=re.sub(r"[^a-z0-9]+", "-", finding.title.strip().lower()).strip("-"),
                diagnosis="Service Signals Inconclusive"
                if scope == "service"
                and finding.title == "No Critical Signals Found"
                and any("metric unavailable:" in item for item in bundle.limitations)
                else finding.title,
                likely_cause=_derive_likely_cause(scope, finding, requested_target_kind),
                confidence=_select_confidence(scope, finding, bundle.limitations),
                score=score,
                supporting_findings=[finding],
                evidence_items=_evidence_items_for_hypothesis(bundle, scope, [finding]),
            )
        )
    return hypotheses


def rank_hypotheses(bundle: EvidenceBundle, target: InvestigationTarget) -> list[Hypothesis]:
    return _group_findings_into_hypotheses(bundle, target.scope, target)


def build_investigation_analysis(bundle: EvidenceBundle, target: InvestigationTarget) -> InvestigationAnalysis:
    return InvestigationAnalysis(
        cluster=bundle.cluster,
        scope=target.scope,
        target=f"{bundle.target.kind}/{bundle.target.name}",
        profile=target.profile,
        hypotheses=rank_hypotheses(bundle, target),
        limitations=list(bundle.limitations),
        recommended_next_step=_recommended_next_step(target.scope, target.profile),
        suggested_follow_ups=_follow_ups_for_analysis(bundle, target.scope, target),
    )


def primary_hypothesis(analysis: InvestigationAnalysis) -> Hypothesis:
    return analysis.hypotheses[0]


def secondary_hypotheses(analysis: InvestigationAnalysis, limit: int = 2) -> list[Hypothesis]:
    return analysis.hypotheses[1 : max(limit, 0) + 1]


def close_secondary_hypotheses(analysis: InvestigationAnalysis, score_gap: int = 40) -> list[Hypothesis]:
    lead = primary_hypothesis(analysis)
    return [
        item
        for item in secondary_hypotheses(analysis)
        if (lead.score - item.score) <= score_gap
    ]


def adjusted_confidence_from_hypotheses(analysis: InvestigationAnalysis, score_gap: int = 40) -> ConfidenceType:
    lead = primary_hypothesis(analysis)
    if not close_secondary_hypotheses(analysis, score_gap=score_gap):
        return lead.confidence
    if lead.confidence == "high":
        return "medium"
    if lead.confidence == "medium":
        return "low"
    return "low"


def ambiguity_limitations_from_hypotheses(analysis: InvestigationAnalysis, score_gap: int = 40) -> list[str]:
    alternatives = close_secondary_hypotheses(analysis, score_gap=score_gap)
    if not alternatives:
        return []
    alternative_names = ", ".join(item.diagnosis for item in alternatives[:2])
    return [
        f"multiple plausible causes remain; alternative hypotheses include {alternative_names}"
    ]


def follow_ups_from_hypotheses(analysis: InvestigationAnalysis, score_gap: int = 40) -> list[str]:
    alternatives = close_secondary_hypotheses(analysis, score_gap=score_gap)
    if not alternatives:
        return []
    return [
        "Validate the leading hypothesis against the next most plausible cause before taking write actions."
    ]


def rendered_evidence_from_hypothesis(hypothesis: Hypothesis) -> list[str]:
    return [
        item.summary if not item.detail else f"{item.summary} - {item.detail}"
        for item in hypothesis.evidence_items
    ]
