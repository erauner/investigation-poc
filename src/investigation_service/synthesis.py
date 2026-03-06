import re

from .event_fingerprints import fingerprint_event, parse_compact_event_text
from .models import (
    CollectContextRequest,
    CollectedContextResponse,
    ConfidenceType,
    EvidenceItem,
    NormalizedInvestigationRequest,
    RootCauseReport,
)
from .routing import scope_from_target

_SOURCE_PRIORITY = {
    "k8s": 50,
    "events": 40,
    "prometheus": 30,
    "logs": 20,
    "heuristic": 10,
}

_SCOPE_TITLE_PRIORITY = {
    "workload": {
        "Container Restart Failure Details": 80,
        "Crash Loop Detected": 70,
        "Possible OOM Condition": 65,
        "Target Not Found": 60,
        "Error-like Log Patterns": 20,
        "Pod Restarts Increasing": 15,
        "No Critical Signals Found": 0,
    },
    "service": {
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


def _request_scope(request: NormalizedInvestigationRequest | CollectContextRequest) -> tuple[str, str]:
    if isinstance(request, NormalizedInvestigationRequest):
        return request.scope, request.profile
    return scope_from_target(request.target, request.profile), request.profile


def _finding_score(scope: str, finding) -> int:
    return (
        _SEVERITY_PRIORITY.get(finding.severity, 0)
        + _SOURCE_PRIORITY.get(finding.source, 0)
        + _SCOPE_TITLE_PRIORITY.get(scope, {}).get(finding.title, 0)
    )


def _ranked_findings(context: CollectedContextResponse, scope: str) -> list:
    return sorted(context.findings, key=lambda item: _finding_score(scope, item), reverse=True)


def _select_confidence(scope: str, lead, limitations: list[str]) -> ConfidenceType:
    limitation_penalty = 0
    if limitations:
        limitation_penalty += 1
    if any("metrics unavailable" in item or "metric unavailable" in item for item in limitations):
        limitation_penalty += 1
    if any("query failed" in item or "target" in item and "failed" in item for item in limitations):
        limitation_penalty += 1

    base_score = 0
    if lead.title in {"Container Restart Failure Details", "Node Not Ready", "Service Returning 5xx Responses"}:
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


def _derive_likely_cause(scope: str, lead) -> str | None:
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
    if lead.title == "Crash Loop Detected":
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


def _recommended_next_step(scope: str, profile: str) -> str:
    if scope == "service":
        return "Inspect service dashboards, recent deploys, and upstream or downstream dependencies before changing traffic handling."
    if scope == "node":
        return "Inspect allocatable vs requests, top consumers, and recent node condition changes before taking capacity actions."
    if profile == "otel-pipeline":
        return "Verify collector ingestion, exporter health, and recent telemetry pipeline changes before restarting components."
    return "Confirm the failure with describe output, recent logs, and rollout history before taking write actions."


def build_primary_evidence(context: CollectedContextResponse, scope: str) -> list[EvidenceItem]:
    evidence_items = [
        EvidenceItem(
            fingerprint=f"finding|{scope}|{re.sub(r'\\s+', ' ', item.title.strip().lower())}|{re.sub(r'\\s+', ' ', item.evidence.strip().lower())}",
            source=item.source,
            kind="finding",
            severity=item.severity,
            summary=f"{item.source}: {item.title}",
            detail=item.evidence,
        )
        for item in _ranked_findings(context, scope)[:5]
    ]
    if context.events and context.events != ["no related events"]:
        first_event = context.events[0]
        reason, message = parse_compact_event_text(first_event)
        event_item = EvidenceItem(
            fingerprint=fingerprint_event(
                resource_kind=context.target.kind,
                namespace=context.target.namespace,
                name=context.target.name,
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
    return evidence_items


def _selected_evidence(evidence_items: list[EvidenceItem]) -> list[str]:
    rendered = [item.summary if not item.detail else f"{item.summary} - {item.detail}" for item in evidence_items]
    return rendered


def _follow_ups(context: CollectedContextResponse, scope: str) -> list[str]:
    follow_ups = list(context.enrichment_hints)
    if any("logs unavailable" in item for item in context.limitations):
        follow_ups.append("Fetch full pod logs or a previous container log stream to confirm the failure path.")
    if any("metrics unavailable" in item or "metric unavailable" in item for item in context.limitations):
        follow_ups.append("Use observability tooling for metrics, traces, or dashboards before making a change.")
    if scope == "service":
        follow_ups.append("Check whether a recent rollout or upstream dependency change lines up with the service degradation.")
    if scope == "node":
        follow_ups.append("Review top memory consumers and recent scheduling pressure on the affected node.")
    return sorted(set(follow_ups))


def build_root_cause_report(
    context: CollectedContextResponse, request: NormalizedInvestigationRequest | CollectContextRequest
) -> RootCauseReport:
    scope, profile = _request_scope(request)
    ranked_findings = _ranked_findings(context, scope)
    lead = ranked_findings[0]
    evidence_items = build_primary_evidence(context, scope)

    return RootCauseReport(
        scope=scope,
        target=f"{context.target.kind}/{context.target.name}",
        diagnosis=lead.title,
        likely_cause=_derive_likely_cause(scope, lead),
        confidence=_select_confidence(scope, lead, context.limitations),
        evidence=_selected_evidence(evidence_items),
        evidence_items=evidence_items,
        limitations=context.limitations,
        recommended_next_step=_recommended_next_step(scope, profile),
        suggested_follow_ups=_follow_ups(context, scope),
    )
