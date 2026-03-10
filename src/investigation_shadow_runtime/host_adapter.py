from __future__ import annotations

import json
import re

from investigation_service.event_fingerprints import parse_compact_event_text
from investigation_service.models import EvidenceItem, FindUnhealthyPodRequest, InvestigationReport, InvestigationReportRequest
from investigation_service.settings import get_default_lookback_minutes
from investigation_service.tools import find_unhealthy_pod

_RESOURCE_PATTERN = re.compile(
    r"(?P<kind>pod|deployment|statefulset|daemonset|job|service|backend|frontend|cluster)/(?P<name>[a-z0-9][a-z0-9-]*)",
    re.IGNORECASE,
)
_NAMESPACE_PATTERN = re.compile(r"\bnamespace\s+(?P<namespace>[a-z0-9][a-z0-9-]*)\b", re.IGNORECASE)
_CLUSTER_PATTERN = re.compile(r"\bcluster\s+(?P<cluster>[a-z0-9][a-z0-9-]*)\b", re.IGNORECASE)
_LOOKBACK_PATTERN = re.compile(r"\b(?P<minutes>\d{1,3})\s*minute[s]?\b", re.IGNORECASE)


def _normalized_lines(task: str) -> list[str]:
    return [line.strip() for line in task.splitlines() if line.strip()]


def _field_map(task: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in _normalized_lines(task):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip().lower()] = value.strip()
    return fields


def _explicit_target(task: str) -> str | None:
    match = _RESOURCE_PATTERN.search(task)
    if match is None:
        return None
    return f"{match.group('kind').lower()}/{match.group('name')}"


def _service_name(task: str, target: str | None) -> str | None:
    if target and target.startswith("service/"):
        return target.split("/", 1)[1]
    fields = _field_map(task)
    service_name = fields.get("service")
    if service_name:
        return service_name
    match = re.search(r"\bservice\s+(?P<name>[a-z0-9][a-z0-9-]*)\b", task, re.IGNORECASE)
    if match is None:
        return None
    return match.group("name")


def _cluster(task: str) -> str | None:
    fields = _field_map(task)
    if "cluster" in fields:
        return fields["cluster"]
    match = _CLUSTER_PATTERN.search(task)
    if match is None:
        return None
    return match.group("cluster")


def _namespace(task: str) -> str | None:
    fields = _field_map(task)
    if "namespace" in fields:
        return fields["namespace"]
    match = _NAMESPACE_PATTERN.search(task)
    if match is None:
        return None
    return match.group("namespace")


def _lookback_minutes(task: str) -> int:
    match = _LOOKBACK_PATTERN.search(task)
    if match is None:
        return get_default_lookback_minutes()
    value = int(match.group("minutes"))
    return min(max(value, 1), 240)


def _parsed_json_request(task: str) -> InvestigationReportRequest | None:
    stripped = task.strip()
    if not stripped.startswith("{"):
        return None
    payload = json.loads(stripped)
    return InvestigationReportRequest.model_validate(payload)


def _resolve_vague_target(task: str, *, cluster: str | None, namespace: str | None, target: str | None) -> str | None:
    if target is not None or namespace is None:
        return target
    lowered = task.lower()
    if "unhealthy pod" not in lowered and "unhealthy workload" not in lowered:
        return target
    response = find_unhealthy_pod(
        FindUnhealthyPodRequest(
            cluster=cluster,
            namespace=namespace,
        )
    )
    if response.candidate is None:
        raise ValueError(f"no unhealthy pod found in namespace {namespace}")
    return response.candidate.target


def parse_shadow_task(task: str) -> InvestigationReportRequest:
    parsed_json = _parsed_json_request(task)
    if parsed_json is not None:
        return parsed_json

    fields = _field_map(task)
    target = _explicit_target(task)
    namespace = _namespace(task)
    cluster = _cluster(task)
    lookback_minutes = _lookback_minutes(task)
    alertname = fields.get("alert")
    if alertname is None:
        alertname = fields.get("alertname")

    service_name = _service_name(task, target)
    node_name = fields.get("node")
    if target is None and "pod" in fields:
        target = f"pod/{fields['pod']}"
    if node_name is None:
        node_match = re.search(r"\bnode\s+(?P<name>[a-z0-9][a-z0-9-]*)\b", task, re.IGNORECASE)
        if node_match is not None and "node-level alert" in task.lower():
            node_name = node_match.group("name")

    profile = "workload"
    if node_name is not None or (target and target.startswith("node/")):
        profile = "node"
        if target is None and node_name is not None:
            target = f"node/{node_name}"
    elif service_name is not None or (target and target.startswith("service/")):
        profile = "service"
        if target is None and service_name is not None:
            target = f"service/{service_name}"

    target = _resolve_vague_target(task, cluster=cluster, namespace=namespace, target=target)

    return InvestigationReportRequest(
        cluster=cluster,
        namespace=namespace,
        target=target,
        profile=profile,
        service_name=service_name,
        lookback_minutes=lookback_minutes,
        include_related_data=True,
        correlation_window_minutes=max(60, lookback_minutes),
        correlation_limit=10,
        alertname=alertname,
        labels={},
        annotations={},
        node_name=node_name,
    )


def format_shadow_report(report: InvestigationReport) -> str:
    sections = [
        ("Diagnosis", report.diagnosis),
        ("Evidence", _bullet_lines(_evidence_lines(report))),
        ("Related Data", _related_data_lines(report)),
        ("Limitations", _bullet_lines(report.limitations, fallback="None reported.")),
        ("Recommended next step", report.recommended_next_step),
    ]
    rendered = []
    for heading, body in sections:
        rendered.append(f"## {heading}\n{body}".rstrip())
    return "\n\n".join(rendered).strip()


def _evidence_lines(report: InvestigationReport) -> list[str]:
    if report.evidence_items:
        rendered: list[str] = []
        seen: set[str] = set()
        for item in report.evidence_items:
            line = _format_evidence_item(item)
            normalized = line.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            rendered.append(line)
        if rendered:
            return _collapse_overlapping_evidence(rendered)

    rendered = []
    seen: set[str] = set()
    for item in report.evidence:
        line = _sanitize_evidence_text(item)
        normalized = line.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        rendered.append(line)
    return _collapse_overlapping_evidence(rendered)


def _format_evidence_item(item: EvidenceItem) -> str:
    summary = _sanitize_evidence_text(item.summary)
    detail = _sanitize_evidence_text(item.detail or "")
    if summary.startswith("events: "):
        summary = summary.removeprefix("events: ").strip()
    if item.kind == "event" and detail:
        extracted = _extract_event_detail(item.detail or "")
        if extracted:
            return f"{summary}: {extracted}"
        reason, message = parse_compact_event_text(detail)
        return f"{summary}: {reason} - {message}"
    if detail and _should_inline_detail(detail):
        return f"{summary} - {detail}"
    return summary


def _sanitize_evidence_text(value: str) -> str:
    text = " ".join(value.split())
    text = text.replace(" - # The following events (YAML format) were found:", "")
    text = text.replace("# The following events (YAML format) were found:", "")
    return text.strip()


def _extract_event_detail(detail: str) -> str | None:
    normalized = _sanitize_evidence_text(detail)
    patterns = [
        r"(Back-off restarting failed container .*?)(?:\s+Namespace:|$)",
        r"(CrashLoopBackOff.*?)(?:\s+Namespace:|$)",
        r"(ImagePullBackOff.*?)(?:\s+Namespace:|$)",
        r"(OOMKilled.*?)(?:\s+Namespace:|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match is not None:
            return match.group(1).strip()

    yaml_warning = re.search(
        r"Message:\s+(.+?)\s+Namespace:.*?Reason:\s+([A-Za-z0-9]+).*?Type:\s+Warning",
        normalized,
        re.IGNORECASE,
    )
    if yaml_warning is not None:
        message = yaml_warning.group(1).strip()
        reason = yaml_warning.group(2).strip()
        return f"{reason} - {message}"

    return None


def _should_inline_detail(detail: str) -> bool:
    if not detail:
        return False
    if len(detail) > 180:
        return False
    if "InvolvedObject:" in detail or "Timestamp:" in detail:
        return False
    return True


def _collapse_overlapping_evidence(items: list[str]) -> list[str]:
    if len(items) < 2:
        return items

    lowered = [item.lower() for item in items]
    keep = [True] * len(items)
    has_backoff_event = any("back-off restarting failed container" in item for item in lowered)

    if has_backoff_event:
        for idx, item in enumerate(lowered):
            if "crash loop detected" in item and "back-off restarting failed container" not in item:
                keep[idx] = False

    collapsed = [item for item, include in zip(items, keep, strict=False) if include]
    return collapsed or items


def _bullet_lines(items: list[str], *, fallback: str = "None.") -> str:
    if not items:
        return fallback
    return "\n".join(f"- {item}" for item in items)


def _related_data_lines(report: InvestigationReport) -> str:
    if report.related_data:
        lines = []
        for item in report.related_data:
            lines.append(f"- {item.timestamp} {item.summary}")
        return "\n".join(lines)
    if report.related_data_note:
        return report.related_data_note
    return "No related data reported."
