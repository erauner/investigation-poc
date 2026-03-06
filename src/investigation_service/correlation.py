import json
from datetime import datetime, timedelta, timezone
import re

from .k8s_adapter import (
    get_events,
    get_pods_for_node,
    get_service_related_deployments,
    resolve_runtime_target,
    resolve_target,
)
from .models import CollectCorrelatedChangesRequest, CorrelatedChange, CorrelatedChangesResponse
from .tools import _canonical_target, _scope_from_target


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _within_window(raw: str | None, lookback_minutes: int, anchor_timestamp: str | None = None) -> bool:
    parsed = _parse_timestamp(raw)
    if parsed is None:
        return False
    anchor = _parse_timestamp(anchor_timestamp) or datetime.now(timezone.utc)
    cutoff = anchor - timedelta(minutes=max(lookback_minutes, 1))
    return cutoff <= parsed <= anchor


def _event_timestamp(event: dict) -> str:
    return (
        event.get("eventTime")
        or event.get("lastTimestamp")
        or event.get("firstTimestamp")
        or event.get("metadata", {}).get("creationTimestamp")
        or ""
    )


def _confidence_for_reason(reason: str) -> str:
    if reason in {"BackOff", "CrashLoopBackOff", "Failed", "FailedMount", "NodeNotReady", "Killing"}:
        return "high"
    if reason in {"ScalingReplicaSet", "SuccessfulCreate", "Pulling", "Pulled", "Scheduled"}:
        return "medium"
    return "low"


def _is_meaningful_event(reason: str, message: str, scope: str) -> bool:
    normalized_reason = reason or ""
    normalized_message = (message or "").lower()
    if scope == "workload":
        allowed = {
            "BackOff",
            "CrashLoopBackOff",
            "Failed",
            "FailedMount",
            "FailedCreatePodSandBox",
            "Killing",
            "Pulled",
            "Pulling",
            "Created",
            "Started",
            "SuccessfulCreate",
            "ScalingReplicaSet",
        }
        if normalized_reason in allowed:
            return True
        keywords = ("rollout", "image", "restart", "restarted", "pulled", "created", "killing", "back-off", "failed")
        return any(keyword in normalized_message for keyword in keywords)
    if scope == "service":
        allowed = {
            "ScalingReplicaSet",
            "SuccessfulCreate",
            "SuccessfulDelete",
            "Created",
            "Updated",
            "Sync",
            "Reloaded",
            "ConfigReload",
        }
        if normalized_reason in allowed:
            return True
        keywords = ("rollout", "scaled", "applied", "updated", "configured", "reloaded", "deployment", "replicaset")
        return any(keyword in normalized_message for keyword in keywords)
    if scope == "node":
        return True
    return False


def _change_from_event(event: dict, relation: str) -> CorrelatedChange:
    involved = event.get("involvedObject", {})
    reason = event.get("reason") or "Event"
    message = event.get("message") or reason
    return CorrelatedChange(
        fingerprint=(
            f"event|{(involved.get('kind') or 'event').lower()}|"
            f"{involved.get('namespace') or event.get('metadata', {}).get('namespace') or 'cluster'}|"
            f"{involved.get('name') or 'unknown'}|{_normalize_text(reason)}|{_normalize_text(message)}"
        ),
        timestamp=_event_timestamp(event),
        source="k8s_event",
        resource_kind=(involved.get("kind") or "Event").lower(),
        namespace=involved.get("namespace") or event.get("metadata", {}).get("namespace"),
        name=involved.get("name") or "unknown",
        relation=relation,
        summary=f"{reason}: {message}",
        confidence=_confidence_for_reason(reason),
    )


def _change_from_rollout(item: dict, relation: str) -> CorrelatedChange:
    images = item.get("images", [])
    image_text = f" images={','.join(images)}" if images else ""
    summary = f"Recent rollout candidate for {item.get('kind', 'deployment')}/{item.get('name', 'unknown')}.{image_text}".strip()
    return CorrelatedChange(
        fingerprint=(
            f"rollout|{item.get('kind', 'deployment')}|{item.get('namespace') or 'cluster'}|"
            f"{item.get('name', 'unknown')}|{_normalize_text(','.join(images))}"
        ),
        timestamp=item.get("timestamp", ""),
        source="rollout",
        resource_kind=item.get("kind", "deployment"),
        namespace=item.get("namespace"),
        name=item.get("name", "unknown"),
        relation=relation,
        summary=summary,
        confidence="medium",
    )


def _change_from_scheduled_pod(item: dict) -> CorrelatedChange:
    summary = f"Pod scheduled onto node recently: {item.get('namespace')}/{item.get('name')}"
    return CorrelatedChange(
        fingerprint=f"scheduled_pod|{item.get('namespace') or 'default'}|{item.get('name', 'unknown')}",
        timestamp=item.get("creationTimestamp", ""),
        source="rollout",
        resource_kind="pod",
        namespace=item.get("namespace"),
        name=item.get("name", "unknown"),
        relation="same_node",
        summary=summary,
        confidence="low",
    )


def _score(change: CorrelatedChange) -> int:
    relation_weight = {
        "direct": 500,
        "same_workload": 400,
        "same_service": 350,
        "same_node": 300,
        "namespace": 200,
        "cluster": 100,
    }[change.relation]
    source_weight = {
        "k8s_event": 60,
        "rollout": 50,
        "config_change": 40,
        "argocd": 30,
        "prometheus_rule": 20,
    }[change.source]
    confidence_weight = {"high": 30, "medium": 20, "low": 10}[change.confidence]
    timestamp_weight = int(_parse_timestamp(change.timestamp).timestamp()) if _parse_timestamp(change.timestamp) else 0
    return relation_weight + source_weight + confidence_weight + timestamp_weight


def _workload_changes(target, lookback_minutes: int, anchor_timestamp: str | None) -> list[CorrelatedChange]:
    changes: list[CorrelatedChange] = []
    for event in get_events(namespace=target.namespace, involved_kind=target.kind, involved_name=target.name, limit=20):
        reason = event.get("reason") or ""
        message = event.get("message") or ""
        if _within_window(_event_timestamp(event), lookback_minutes, anchor_timestamp) and _is_meaningful_event(
            reason, message, "workload"
        ):
            changes.append(_change_from_event(event, "direct"))
    return changes


def _service_changes(
    target, service_name: str, lookback_minutes: int, anchor_timestamp: str | None
) -> tuple[list[CorrelatedChange], list[str]]:
    changes: list[CorrelatedChange] = []
    limitations: list[str] = []
    for event in get_events(namespace=target.namespace, involved_kind="Service", involved_name=target.name, limit=20):
        if _within_window(_event_timestamp(event), lookback_minutes, anchor_timestamp) and _is_meaningful_event(
            event.get("reason") or "", event.get("message") or "", "service"
        ):
            changes.append(_change_from_event(event, "direct"))

    deployments = get_service_related_deployments(target.namespace or "", service_name)
    if not deployments:
        limitations.append("no related deployments inferred from service selector")
    for item in deployments:
        if _within_window(item.get("timestamp"), lookback_minutes, anchor_timestamp):
            changes.append(_change_from_rollout(item, "same_service"))
    return changes, limitations


def _node_changes(target, lookback_minutes: int, anchor_timestamp: str | None) -> list[CorrelatedChange]:
    changes: list[CorrelatedChange] = []
    for event in get_events(namespace=None, involved_kind="Node", involved_name=target.name, limit=20):
        if _within_window(_event_timestamp(event), lookback_minutes, anchor_timestamp) and _is_meaningful_event(
            event.get("reason") or "", event.get("message") or "", "node"
        ):
            changes.append(_change_from_event(event, "direct"))
    for item in get_pods_for_node(target.name, limit=10):
        if _within_window(item.get("creationTimestamp"), lookback_minutes, anchor_timestamp):
            changes.append(_change_from_scheduled_pod(item))
    return changes


def collect_correlated_changes(req: CollectCorrelatedChangesRequest) -> CorrelatedChangesResponse:
    target_text = _canonical_target(req.target, req.profile, req.service_name)
    scope = _scope_from_target(target_text, req.profile)
    resolved = resolve_runtime_target(resolve_target(req.namespace, target_text))
    changes: list[CorrelatedChange] = []
    limitations: list[str] = []

    if scope == "service":
        service_name = req.service_name or resolved.name
        changes, service_limitations = _service_changes(resolved, service_name, req.lookback_minutes, req.anchor_timestamp)
        limitations.extend(service_limitations)
    elif scope == "node":
        changes = _node_changes(resolved, req.lookback_minutes, req.anchor_timestamp)
    else:
        changes = _workload_changes(resolved, req.lookback_minutes, req.anchor_timestamp)

    if not changes:
        limitations.append("no correlated changes found in the requested time window")

    ranked = sorted(changes, key=_score, reverse=True)[: req.limit]
    return CorrelatedChangesResponse(
        scope=scope,
        target=f"{resolved.kind}/{resolved.name}",
        changes=ranked,
        limitations=sorted(set(limitations)),
    )
