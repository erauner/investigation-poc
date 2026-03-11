from __future__ import annotations

import json
import re

from investigation_service.models import InvestigationReport, InvestigationReportRequest
from investigation_service.presentation import render_presentation_markdown
from investigation_service.reporting import resolve_primary_target
from investigation_service.settings import get_default_lookback_minutes

_RESOURCE_PATTERN = re.compile(
    r"(?P<kind>pod|deployment|statefulset|daemonset|job|service|backend|frontend|cluster|node)/(?P<name>[a-z0-9][a-z0-9-]*)",
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
        if target is None and node_name is not None:
            target = f"node/{node_name}"
        if node_name is None and target is not None:
            node_name = target.split("/", 1)[1]
    elif service_name is not None or (target and target.startswith("service/")):
        profile = "service"
        if target is None and service_name is not None:
            target = f"service/{service_name}"

    request = InvestigationReportRequest(
        cluster=cluster,
        namespace=namespace,
        target=target,
        question=task,
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
    resolved = resolve_primary_target(request)

    return request.model_copy(
        update={
            "cluster": resolved.cluster or request.cluster,
            "namespace": resolved.namespace or request.namespace,
            "target": resolved.target,
            "profile": resolved.profile,
            "service_name": resolved.service_name or request.service_name,
            "node_name": resolved.node_name or request.node_name,
        }
    )


def format_shadow_report(report: InvestigationReport) -> str:
    return render_presentation_markdown(report, profile="operator_summary")
