from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
import asyncio
import json
import math
import re
import threading

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from investigation_service.cluster_registry import resolve_cluster
from investigation_service.k8s_adapter import (
    normalize_k8s_object_payload,
    pick_runtime_pod_for_workload,
    resolve_runtime_target,
    resolve_target,
    summarize_top_pods_for_node,
    summarize_service_topology,
)
from investigation_service.models import StepExecutionInputs, TargetRef
from investigation_service.prom_adapter import (
    node_metric_queries,
    select_best_service_metric_family,
    service_metric_query_families,
    service_metric_range_query_families,
)
from investigation_service.settings import (
    get_alertmanager_mcp_url,
    get_alertmanager_url,
    get_cluster_name,
    get_default_cluster_alias,
    get_kubernetes_mcp_url,
    get_log_tail_lines,
    get_loki_mcp_url,
    get_loki_url,
    get_peer_mcp_timeout_seconds,
    get_prometheus_url,
    get_prometheus_mcp_url,
)


class PeerMcpError(RuntimeError):
    pass


def _peer_error_message(exc: BaseException) -> str:
    if isinstance(exc, PeerMcpError):
        return str(exc)
    nested = getattr(exc, "exceptions", None) or []
    for child in nested:
        message = _peer_error_message(child)
        if message:
            return message
    return str(exc)


@dataclass
class WorkloadRuntimeSnapshot:
    cluster_alias: str
    target: TargetRef
    object_state: dict[str, Any]
    events: list[str]
    log_excerpt: str
    limitations: list[str] = field(default_factory=list)
    tool_path: list[str] = field(default_factory=list)
    runtime_pod_name: str | None = None


@dataclass
class ServiceMetricsSnapshot:
    cluster_alias: str
    target: TargetRef
    metrics: dict[str, Any]
    limitations: list[str] = field(default_factory=list)
    tool_path: list[str] = field(default_factory=list)


@dataclass
class ServiceRuntimeSnapshot:
    cluster_alias: str
    target: TargetRef
    object_state: dict[str, Any]
    events: list[str]
    limitations: list[str] = field(default_factory=list)
    tool_path: list[str] = field(default_factory=list)


@dataclass
class LokiLogsSnapshot:
    cluster_alias: str
    target: TargetRef
    log_excerpt: str
    limitations: list[str] = field(default_factory=list)
    tool_path: list[str] = field(default_factory=list)


@dataclass
class AlertmanagerAlertSnapshot:
    cluster_alias: str
    alerts: list[dict[str, Any]]
    limitations: list[str] = field(default_factory=list)
    tool_path: list[str] = field(default_factory=list)


@dataclass
class NodeMetricsSnapshot:
    cluster_alias: str
    target: TargetRef
    metrics: dict[str, Any]
    limitations: list[str] = field(default_factory=list)
    tool_path: list[str] = field(default_factory=list)


@dataclass
class NodeRuntimeSnapshot:
    cluster_alias: str
    target: TargetRef
    object_state: dict[str, Any]
    events: list[str]
    limitations: list[str] = field(default_factory=list)
    tool_path: list[str] = field(default_factory=list)


@dataclass
class NodePodSummarySnapshot:
    cluster_alias: str
    target: TargetRef
    top_pods_by_memory_request: list[dict[str, Any]]
    limitations: list[str] = field(default_factory=list)
    tool_path: list[str] = field(default_factory=list)


def _extract_content(result: Any) -> Any:
    if getattr(result, "structuredContent", None) is not None:
        return result.structuredContent
    content = getattr(result, "content", None) or []
    if not content:
        return None
    first = content[0]
    text = getattr(first, "text", None)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _extract_name(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return None
    return item.get("name") or item.get("metadata", {}).get("name")


def _normalize_events(target: TargetRef, raw: Any) -> list[str]:
    if isinstance(raw, dict):
        candidates = raw.get("events") or raw.get("items") or []
    elif isinstance(raw, list):
        candidates = raw
    elif raw:
        candidates = [raw]
    else:
        candidates = []

    rendered = [str(item) for item in candidates]
    filtered = [item for item in rendered if target.name in item]
    return filtered or rendered or ["no related events"]


def _normalize_logs(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("logs", "log", "content", "text"):
            value = raw.get(key)
            if isinstance(value, str):
                return value
    return "" if raw is None else str(raw)


def _normalize_loki_logs(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        for key in ("log_excerpt", "logs", "content", "text"):
            value = raw.get(key)
            if isinstance(value, str):
                return value.strip()
        if "values" in raw and isinstance(raw["values"], list):
            return _normalize_loki_logs(raw["values"])
        for key in ("lines", "entries", "items", "result"):
            value = raw.get(key)
            if value is not None:
                return _normalize_loki_logs(value)
        if "streams" in raw:
            return _normalize_loki_logs(raw["streams"])
        if "data" in raw:
            return _normalize_loki_logs(raw["data"])
        for key in ("message", "line"):
            value = raw.get(key)
            if isinstance(value, str):
                return value.strip()
    if isinstance(raw, list):
        if len(raw) == 2 and not isinstance(raw[0], (list, dict)) and isinstance(raw[1], str):
            return raw[1].strip()
        lines: list[str] = []
        for item in raw:
            normalized = _normalize_loki_logs(item)
            if normalized:
                lines.append(normalized)
        return "\n".join(lines).strip()
    return str(raw).strip()


def _format_loki_window(minutes: int | None) -> str:
    lookback_minutes = max(minutes or 15, 1)
    return (
        datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    ).isoformat().replace("+00:00", "Z")


def _format_window_end() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_workload_loki_query(target: TargetRef, runtime_pod_name: str) -> str:
    return f'{{namespace="{target.namespace}",pod="{runtime_pod_name}"}}'


def _normalize_alertmanager_alerts(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("alerts", raw.get("data", raw))
    if not isinstance(raw, list):
        raise PeerMcpError("alertmanager_list_alerts returned malformed payload")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        labels = item.get("labels") or {}
        annotations = item.get("annotations") or {}
        if not isinstance(labels, dict) or not isinstance(annotations, dict):
            continue
        fingerprint = item.get("fingerprint")
        starts_at = item.get("startsAt")
        ends_at = item.get("endsAt")
        key = (
            fingerprint,
            tuple(sorted((str(k), str(v)) for k, v in labels.items())),
            starts_at,
            ends_at,
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "fingerprint": fingerprint,
                "status": item.get("status") or {},
                "labels": {str(k): str(v) for k, v in labels.items()},
                "annotations": {str(k): str(v) for k, v in annotations.items()},
                "startsAt": starts_at,
                "endsAt": ends_at,
                "updatedAt": item.get("updatedAt"),
                "generatorURL": item.get("generatorURL"),
            }
        )
    normalized.sort(key=lambda item: (item.get("startsAt") or "", item.get("fingerprint") or ""), reverse=True)
    return normalized


def _alert_identity_filters(inputs: StepExecutionInputs) -> dict[str, str]:
    filters: dict[str, str] = {}
    if not inputs.alertname:
        raise PeerMcpError("alertmanager peer transport requires alertname")
    filters["alertname"] = inputs.alertname
    labels = inputs.labels or {}
    allowed_keys = {
        "namespace",
        "kubernetes_namespace",
        "exported_namespace",
        "pod",
        "pod_name",
        "kubernetes_pod_name",
        "service",
        "service_name",
        "deployment",
        "deployment_name",
        "kubernetes_deployment_name",
        "statefulset",
        "statefulset_name",
        "kubernetes_statefulset_name",
        "node",
        "node_name",
        "kubernetes_node",
        "instance",
    }
    for key, value in labels.items():
        if key in allowed_keys and value:
            filters[key] = value
    if inputs.namespace and not any(key in filters for key in {"namespace", "kubernetes_namespace", "exported_namespace"}):
        filters["namespace"] = inputs.namespace
    return filters


def _peer_alertmanager_routing_unsupported(cluster, requested_cluster: str | None) -> bool:
    requested_alias = (requested_cluster or "").strip().lower() or None
    local_aliases = {
        alias.strip().lower()
        for alias in (get_default_cluster_alias(), get_cluster_name(), "local-kind", "current-context")
        if alias
    }
    resolved_alias = (cluster.alias or "").strip().lower()
    if resolved_alias in local_aliases or (requested_alias and requested_alias in local_aliases):
        return False
    return bool(cluster.alertmanager_url and cluster.alertmanager_url != get_alertmanager_url()) or resolved_alias not in local_aliases


def _service_loki_pod_candidates(object_state: dict[str, Any] | None, *, max_pods: int = 5) -> tuple[str, ...]:
    matched_pods = (object_state or {}).get("matchedPods") or []
    names = sorted(
        {
            str(item.get("name")).strip()
            for item in matched_pods
            if isinstance(item, dict) and isinstance(item.get("name"), str) and item.get("name").strip()
        }
    )
    return tuple(names[:max_pods])


def _service_loki_app_candidates(object_state: dict[str, Any] | None, target: TargetRef) -> tuple[str, ...]:
    selector = (object_state or {}).get("selector") or {}
    candidates: list[str] = []
    value = selector.get("app.kubernetes.io/name")
    if isinstance(value, str) and value.strip():
        candidates.append(value.strip())
    return tuple(dict.fromkeys(candidates))


def _build_service_loki_queries(target: TargetRef, object_state: dict[str, Any] | None = None) -> tuple[str, ...]:
    queries: list[str] = []
    pod_candidates = _service_loki_pod_candidates(object_state)
    if pod_candidates:
        escaped = "|".join(re.escape(name) for name in pod_candidates)
        queries.append(f'{{namespace="{target.namespace}",pod=~"{escaped}"}}')
    for app_candidate in _service_loki_app_candidates(object_state, target):
        queries.append(f'{{namespace="{target.namespace}",app="{app_candidate}"}}')
    return tuple(dict.fromkeys(queries))


def _normalize_metric_value(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value if math.isfinite(value) else None
    if isinstance(raw, str):
        try:
            value = float(raw)
            return value if math.isfinite(value) else None
        except ValueError:
            match = re.search(r"=>\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*@\[", raw)
            if match:
                try:
                    value = float(match.group(1))
                    return value if math.isfinite(value) else None
                except ValueError:
                    return None
            return None
    if isinstance(raw, list):
        if not raw:
            return None
        if len(raw) >= 2:
            return _normalize_metric_value(raw[1])
        return _normalize_metric_value(raw[0])
    if isinstance(raw, dict):
        if "value" in raw:
            return _normalize_metric_value(raw["value"])
        if "result" in raw:
            return _normalize_metric_value(raw["result"])
        if "data" in raw:
            return _normalize_metric_value(raw["data"])
        if "text" in raw:
            return _normalize_metric_value(raw["text"])
    return None


def _latest_non_null_metric_value(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        if "values" in raw and isinstance(raw["values"], list):
            return _latest_non_null_metric_value(raw["values"])
        if "result" in raw and isinstance(raw["result"], list):
            return _latest_non_null_metric_value(raw["result"])
        if "data" in raw:
            return _latest_non_null_metric_value(raw["data"])
        if "text" in raw and isinstance(raw["text"], str):
            matches = re.findall(r"=>\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*@\[", raw["text"])
            if matches:
                try:
                    return float(matches[-1])
                except ValueError:
                    return None
        return _normalize_metric_value(raw)
    if isinstance(raw, list):
        if raw and all(isinstance(item, (list, tuple)) and len(item) >= 2 for item in raw):
            for item in reversed(raw):
                value = _normalize_metric_value(item[1])
                if value is not None:
                    return value
            return None
        for item in reversed(raw):
            value = _latest_non_null_metric_value(item)
            if value is not None:
                return value
        return None
    return _normalize_metric_value(raw)


def _peer_prometheus_routing_unsupported(cluster, requested_cluster: str | None) -> bool:
    requested_alias = (requested_cluster or "").strip().lower() or None
    local_aliases = {
        alias for alias in (get_default_cluster_alias(), get_cluster_name(), "local-kind", "current-context") if alias
    }
    if requested_alias is None:
        return False
    if cluster.alias in local_aliases or requested_alias in local_aliases:
        return False
    if not cluster.prometheus_url or cluster.prometheus_url == get_prometheus_url():
        return False
    return True


def _peer_loki_routing_unsupported(cluster, requested_cluster: str | None) -> bool:
    requested_alias = (requested_cluster or "").strip().lower() or None
    local_aliases = {
        alias for alias in (get_default_cluster_alias(), get_cluster_name(), "local-kind", "current-context") if alias
    }
    if cluster.alias in local_aliases or requested_alias in local_aliases:
        return False
    return True


def _pick_runtime_pod(prefix: str, raw: Any) -> str | None:
    if isinstance(raw, dict):
        items = raw.get("pods") or raw.get("items") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    for item in items:
        name = _extract_name(item)
        if name and (name == prefix or name.startswith(f"{prefix}-")):
            return name
    return None


def _api_version_for_target(target: TargetRef) -> str:
    if target.kind in {"deployment", "statefulset"}:
        return "apps/v1"
    return "v1"


def _normalize_object_state(
    raw_object_state: Any,
    target: TargetRef,
    *,
    runtime_pod_raw: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(raw_object_state, dict):
        return {
            "kind": target.kind,
            "name": target.name,
            "namespace": target.namespace,
            "raw": raw_object_state,
        }
    runtime_pod = None
    if runtime_pod_raw is not None:
        runtime_target = TargetRef(namespace=target.namespace, kind="pod", name=runtime_pod_raw.get("metadata", {}).get("name", ""))
        runtime_pod = normalize_k8s_object_payload(runtime_pod_raw, runtime_target)
    return normalize_k8s_object_payload(raw_object_state, target, runtime_pod=runtime_pod)


class KubernetesMcpClient:
    def __init__(self, *, url: str | None = None, timeout_seconds: float | None = None):
        self.url = url or get_kubernetes_mcp_url()
        self.timeout_seconds = timeout_seconds or get_peer_mcp_timeout_seconds()

    async def _call_tool(self, session: ClientSession, tool_name: str, arguments: dict[str, Any]) -> Any:
        result = await session.call_tool(tool_name, arguments)
        if getattr(result, "isError", False):
            raise PeerMcpError(f"{tool_name} returned MCP error")
        return _extract_content(result)

    async def _collect_async(
        self,
        inputs: StepExecutionInputs,
        *,
        excluded_pod_names: tuple[str, ...] = (),
    ) -> WorkloadRuntimeSnapshot:
        cluster = resolve_cluster(inputs.cluster)
        local_aliases = {
            alias
            for alias in (
                get_default_cluster_alias(),
                get_cluster_name(),
                "local-kind",
            )
            if alias
        }
        if (cluster.kube_context or cluster.kubeconfig_path) and cluster.alias not in local_aliases:
            raise PeerMcpError("peer workload MCP transport does not yet support multicluster kubeconfig routing")
        if not inputs.namespace or not inputs.target:
            raise PeerMcpError("workload peer transport requires namespace and target")

        requested = resolve_target(inputs.namespace, inputs.target, cluster=cluster)
        target = resolve_runtime_target(requested, cluster=cluster)

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tool_path = ["kubernetes-mcp-server"]
                    limitations: list[str] = []

                    raw_object_state = await self._call_tool(
                        session,
                        "resources_get",
                        {
                            "apiVersion": _api_version_for_target(target),
                            "kind": target.kind,
                            "name": target.name,
                            "namespace": target.namespace,
                        },
                    )
                    tool_path.append("resources_get")
                    object_state = _normalize_object_state(raw_object_state, target)

                    events_raw = await self._call_tool(
                        session,
                        "events_list",
                        {"namespace": target.namespace},
                    )
                    tool_path.append("events_list")
                    events = _normalize_events(target, events_raw)

                    log_excerpt = ""
                    if target.kind in {"pod", "deployment", "statefulset"}:
                        pod_name = target.name
                        if target.kind in {"deployment", "statefulset"}:
                            pods_raw = await self._call_tool(
                                session,
                                "pods_list_in_namespace",
                                {"namespace": target.namespace},
                            )
                            tool_path.append("pods_list_in_namespace")
                            if excluded_pod_names:
                                if isinstance(pods_raw, dict):
                                    items = pods_raw.get("items") or []
                                    pods_raw = {
                                        **pods_raw,
                                        "items": [
                                            item
                                            for item in items
                                            if item.get("metadata", {}).get("name") not in excluded_pod_names
                                        ],
                                    }
                                elif isinstance(pods_raw, list):
                                    pods_raw = [
                                        item
                                        for item in pods_raw
                                        if isinstance(item, dict)
                                        and item.get("metadata", {}).get("name") not in excluded_pod_names
                                    ]
                            pod_name = pick_runtime_pod_for_workload(raw_object_state, pods_raw)
                            if not pod_name:
                                raise PeerMcpError(
                                    f"could not resolve runtime pod for {target.kind} target"
                                )
                            runtime_pod_raw = await self._call_tool(
                                session,
                                "resources_get",
                                {
                                    "apiVersion": "v1",
                                    "kind": "Pod",
                                    "name": pod_name,
                                    "namespace": target.namespace,
                                },
                            )
                            tool_path.append("resources_get")
                            object_state = _normalize_object_state(
                                raw_object_state,
                                target,
                                runtime_pod_raw=runtime_pod_raw if isinstance(runtime_pod_raw, dict) else None,
                            )
                        logs_raw = await self._call_tool(
                            session,
                            "pods_log",
                            {
                                "namespace": target.namespace,
                                "name": pod_name,
                                "tail": 200,
                            },
                        )
                        tool_path.append("pods_log")
                        log_excerpt = _normalize_logs(logs_raw)
                        if not log_excerpt:
                            limitations.append("pod logs unavailable for target")

                    return WorkloadRuntimeSnapshot(
                        cluster_alias=cluster.alias,
                        target=target,
                        object_state=object_state,
                        events=events,
                        log_excerpt=log_excerpt,
                        limitations=limitations,
                        tool_path=tool_path,
                        runtime_pod_name=pod_name,
                    )

    def collect_workload_runtime(
        self,
        inputs: StepExecutionInputs,
        *,
        excluded_pod_names: tuple[str, ...] = (),
    ) -> WorkloadRuntimeSnapshot:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running_loop = False
        else:
            running_loop = True

        if running_loop:
            result: dict[str, WorkloadRuntimeSnapshot] = {}
            error: dict[str, Exception] = {}

            def _runner() -> None:
                try:
                    result["snapshot"] = anyio.run(
                        lambda: self._collect_async(inputs, excluded_pod_names=excluded_pod_names)
                    )
                except Exception as exc:  # pragma: no cover - exercised via live MCP path
                    error["exception"] = exc

            thread = threading.Thread(target=_runner, daemon=True)
            thread.start()
            thread.join()
            if "exception" in error:
                exc = error["exception"]
                if isinstance(exc, PeerMcpError):
                    raise exc
                raise PeerMcpError(_peer_error_message(exc)) from exc
            return result["snapshot"]

        try:
            return anyio.run(lambda: self._collect_async(inputs, excluded_pod_names=excluded_pod_names))
        except PeerMcpError:
            raise
        except Exception as exc:
            raise PeerMcpError(_peer_error_message(exc)) from exc

    async def _collect_service_async(self, inputs: StepExecutionInputs) -> ServiceRuntimeSnapshot:
        cluster = resolve_cluster(inputs.cluster)
        local_aliases = {
            alias for alias in (get_default_cluster_alias(), get_cluster_name(), "local-kind") if alias
        }
        if (cluster.kube_context or cluster.kubeconfig_path) and cluster.alias not in local_aliases:
            raise PeerMcpError("peer service Kubernetes fallback does not yet support multicluster kubeconfig routing")
        namespace = inputs.namespace
        service_name = inputs.service_name
        if not namespace or not service_name:
            raise PeerMcpError("service Kubernetes fallback requires namespace and service_name")

        target = TargetRef(namespace=namespace, kind="service", name=service_name)

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tool_path = ["kubernetes-mcp-server"]
                    raw_object_state = await self._call_tool(
                        session,
                        "resources_get",
                        {
                            "apiVersion": "v1",
                            "kind": "Service",
                            "name": service_name,
                            "namespace": namespace,
                        },
                    )
                    tool_path.append("resources_get")
                    object_state = _normalize_object_state(raw_object_state, target)
                    limitations: list[str] = []
                    try:
                        pods_raw = await self._call_tool(
                            session,
                            "pods_list_in_namespace",
                            {"namespace": namespace},
                        )
                        tool_path.append("pods_list_in_namespace")
                        pods = []
                        if isinstance(pods_raw, dict):
                            pods = pods_raw.get("items") or pods_raw.get("pods") or []
                        elif isinstance(pods_raw, list):
                            pods = pods_raw
                        object_state.update(
                            summarize_service_topology(
                                raw_object_state if isinstance(raw_object_state, dict) else {},
                                [pod for pod in pods if isinstance(pod, dict)],
                            )
                        )
                    except PeerMcpError:
                        limitations.append("peer service Kubernetes fallback could not infer backend topology")
                    events_raw = await self._call_tool(session, "events_list", {"namespace": namespace})
                    tool_path.append("events_list")
                    events = _normalize_events(target, events_raw)
                    if events == ["no related events"]:
                        limitations.append("peer service Kubernetes fallback returned no related events")
                    return ServiceRuntimeSnapshot(
                        cluster_alias=cluster.alias,
                        target=target,
                        object_state=object_state,
                        events=events,
                        limitations=limitations,
                        tool_path=tool_path,
                    )

    async def _collect_node_async(self, inputs: StepExecutionInputs) -> NodeRuntimeSnapshot:
        cluster = resolve_cluster(inputs.cluster)
        local_aliases = {
            alias for alias in (get_default_cluster_alias(), get_cluster_name(), "local-kind") if alias
        }
        if (cluster.kube_context or cluster.kubeconfig_path) and cluster.alias not in local_aliases:
            raise PeerMcpError("peer node Kubernetes fallback does not yet support multicluster kubeconfig routing")
        if not inputs.node_name:
            raise PeerMcpError("node peer transport requires node_name")

        target = TargetRef(namespace=None, kind="node", name=inputs.node_name)

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tool_path = ["kubernetes-mcp-server"]
                    limitations: list[str] = []

                    raw_object_state = await self._call_tool(
                        session,
                        "resources_get",
                        {
                            "apiVersion": "v1",
                            "kind": "Node",
                            "name": target.name,
                        },
                    )
                    tool_path.append("resources_get")
                    object_state = _normalize_object_state(raw_object_state, target)

                    try:
                        events_raw = await self._call_tool(session, "events_list", {})
                        tool_path.append("events_list")
                        events = _normalize_events(target, events_raw)
                    except PeerMcpError:
                        events = ["no related events"]
                        limitations.append("peer node Kubernetes fallback could not read cluster events")

                    return NodeRuntimeSnapshot(
                        cluster_alias=cluster.alias,
                        target=target,
                        object_state=object_state,
                        events=events,
                        limitations=limitations,
                        tool_path=tool_path,
                    )

    async def _collect_node_top_pods_async(
        self,
        inputs: StepExecutionInputs,
        *,
        limit: int = 5,
    ) -> NodePodSummarySnapshot:
        cluster = resolve_cluster(inputs.cluster)
        local_aliases = {
            alias for alias in (get_default_cluster_alias(), get_cluster_name(), "local-kind") if alias
        }
        if (cluster.kube_context or cluster.kubeconfig_path) and cluster.alias not in local_aliases:
            raise PeerMcpError("peer node Kubernetes fallback does not yet support multicluster kubeconfig routing")
        if not inputs.node_name:
            raise PeerMcpError("node peer transport requires node_name")

        target = TargetRef(namespace=None, kind="node", name=inputs.node_name)

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tool_path = ["kubernetes-mcp-server"]
                    raw = await self._call_tool(
                        session,
                        "resources_list",
                        {
                            "apiVersion": "v1",
                            "kind": "Pod",
                            "fieldSelector": f"spec.nodeName={target.name}",
                        },
                    )
                    tool_path.append("resources_list")
                    items: list[dict[str, Any]]
                    if isinstance(raw, dict):
                        candidate_items = raw.get("items") or raw.get("resources") or raw.get("pods") or []
                        items = [item for item in candidate_items if isinstance(item, dict)]
                    elif isinstance(raw, list):
                        items = [item for item in raw if isinstance(item, dict)]
                    else:
                        items = []
                    top_pods = summarize_top_pods_for_node(items, limit=limit)
                    limitations: list[str] = []
                    if not top_pods:
                        limitations.append("node workload summary unavailable")
                    return NodePodSummarySnapshot(
                        cluster_alias=cluster.alias,
                        target=target,
                        top_pods_by_memory_request=top_pods,
                        limitations=limitations,
                        tool_path=tool_path,
                    )

    def collect_service_runtime(self, inputs: StepExecutionInputs) -> ServiceRuntimeSnapshot:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running_loop = False
        else:
            running_loop = True

        if running_loop:
            result: dict[str, ServiceRuntimeSnapshot] = {}
            error: dict[str, Exception] = {}

            def _runner() -> None:
                try:
                    result["snapshot"] = anyio.run(self._collect_service_async, inputs)
                except Exception as exc:  # pragma: no cover
                    error["exception"] = exc

            thread = threading.Thread(target=_runner, daemon=True)
            thread.start()
            thread.join()
            if "exception" in error:
                exc = error["exception"]
                if isinstance(exc, PeerMcpError):
                    raise exc
                raise PeerMcpError(_peer_error_message(exc)) from exc
            return result["snapshot"]

        try:
            return anyio.run(self._collect_service_async, inputs)
        except PeerMcpError:
            raise
        except Exception as exc:
            raise PeerMcpError(_peer_error_message(exc)) from exc

    def collect_node_runtime(self, inputs: StepExecutionInputs) -> NodeRuntimeSnapshot:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running_loop = False
        else:
            running_loop = True

        if running_loop:
            result: dict[str, NodeRuntimeSnapshot] = {}
            error: dict[str, Exception] = {}

            def _runner() -> None:
                try:
                    result["snapshot"] = anyio.run(self._collect_node_async, inputs)
                except Exception as exc:  # pragma: no cover
                    error["exception"] = exc

            thread = threading.Thread(target=_runner, daemon=True)
            thread.start()
            thread.join()
            if "exception" in error:
                exc = error["exception"]
                if isinstance(exc, PeerMcpError):
                    raise exc
                raise PeerMcpError(_peer_error_message(exc)) from exc
            return result["snapshot"]

        try:
            return anyio.run(self._collect_node_async, inputs)
        except PeerMcpError:
            raise
        except Exception as exc:
            raise PeerMcpError(_peer_error_message(exc)) from exc

    def collect_node_top_pods(
        self,
        inputs: StepExecutionInputs,
        *,
        limit: int = 5,
    ) -> NodePodSummarySnapshot:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running_loop = False
        else:
            running_loop = True

        if running_loop:
            result: dict[str, NodePodSummarySnapshot] = {}
            error: dict[str, Exception] = {}

            def _runner() -> None:
                try:
                    result["snapshot"] = anyio.run(
                        lambda: self._collect_node_top_pods_async(inputs, limit=limit)
                    )
                except Exception as exc:  # pragma: no cover
                    error["exception"] = exc

            thread = threading.Thread(target=_runner, daemon=True)
            thread.start()
            thread.join()
            if "exception" in error:
                exc = error["exception"]
                if isinstance(exc, PeerMcpError):
                    raise exc
                raise PeerMcpError(_peer_error_message(exc)) from exc
            return result["snapshot"]

        try:
            return anyio.run(lambda: self._collect_node_top_pods_async(inputs, limit=limit))
        except PeerMcpError:
            raise
        except Exception as exc:
            raise PeerMcpError(_peer_error_message(exc)) from exc


class PrometheusMcpClient:
    def __init__(self, *, url: str | None = None, timeout_seconds: float | None = None):
        self.url = url or get_prometheus_mcp_url()
        self.timeout_seconds = timeout_seconds or get_peer_mcp_timeout_seconds()

    async def _call_tool(self, session: ClientSession, tool_name: str, arguments: dict[str, Any]) -> Any:
        result = await session.call_tool(tool_name, arguments)
        if getattr(result, "isError", False):
            raise PeerMcpError(f"{tool_name} returned MCP error")
        return _extract_content(result)

    async def _collect_service_async(self, inputs: StepExecutionInputs) -> ServiceMetricsSnapshot:
        cluster = resolve_cluster(inputs.cluster)
        local_aliases = {
            alias for alias in (get_default_cluster_alias(), get_cluster_name(), "local-kind") if alias
        }
        if cluster.prometheus_url and cluster.alias not in local_aliases:
            raise PeerMcpError("peer service Prometheus transport does not yet support multicluster prometheus routing")
        namespace = inputs.namespace
        service_name = inputs.service_name
        if not namespace or not service_name:
            raise PeerMcpError("service peer transport requires namespace and service_name")

        target = TargetRef(namespace=namespace, kind="service", name=service_name)
        query_families = service_metric_query_families(namespace, service_name, inputs.lookback_minutes or 15)

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tool_path = ["prometheus-mcp-server"]
                    family_results: list[tuple[str, dict[str, float | None]]] = []
                    for family_id, queries in query_families:
                        family_metrics: dict[str, float | None] = {}
                        for label, query in queries.items():
                            raw = await self._call_tool(session, "execute_query", {"query": query})
                            tool_path.append("execute_query")
                            family_metrics[label] = _normalize_metric_value(raw)
                        family_results.append((family_id, family_metrics))
                    metrics, limitations = select_best_service_metric_family(family_results)
                    return ServiceMetricsSnapshot(
                        cluster_alias=cluster.alias,
                        target=target,
                        metrics=metrics,
                        limitations=limitations,
                        tool_path=tool_path,
                    )

    async def _collect_service_range_async(
        self,
        inputs: StepExecutionInputs,
        *,
        max_metric_families: int = 1,
    ) -> ServiceMetricsSnapshot:
        cluster = resolve_cluster(inputs.cluster)
        local_aliases = {
            alias for alias in (get_default_cluster_alias(), get_cluster_name(), "local-kind") if alias
        }
        if cluster.prometheus_url and cluster.alias not in local_aliases:
            raise PeerMcpError("peer service Prometheus transport does not yet support multicluster prometheus routing")
        namespace = inputs.namespace
        service_name = inputs.service_name
        if not namespace or not service_name:
            raise PeerMcpError("service peer transport requires namespace and service_name")

        target = TargetRef(namespace=namespace, kind="service", name=service_name)
        query_families = service_metric_range_query_families(namespace, service_name, inputs.lookback_minutes or 15)[:max_metric_families]

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tool_path = ["prometheus-mcp-server"]
                    family_results: list[tuple[str, dict[str, float | None]]] = []
                    end_time = datetime.now(timezone.utc)
                    start_time = end_time - timedelta(minutes=max(inputs.lookback_minutes or 15, 1))
                    start_rfc3339 = start_time.isoformat().replace("+00:00", "Z")
                    end_rfc3339 = end_time.isoformat().replace("+00:00", "Z")
                    for family_id, queries in query_families:
                        family_metrics: dict[str, float | None] = {}
                        for label, query in queries.items():
                            raw = await self._call_tool(
                                session,
                                "execute_range_query",
                                {
                                    "query": query,
                                    "start": start_rfc3339,
                                    "end": end_rfc3339,
                                    "step": "60s",
                                },
                            )
                            tool_path.append("execute_range_query")
                            family_metrics[label] = _latest_non_null_metric_value(raw)
                        family_results.append((family_id, family_metrics))
                    metrics, limitations = select_best_service_metric_family(family_results)
                    return ServiceMetricsSnapshot(
                        cluster_alias=cluster.alias,
                        target=target,
                        metrics=metrics,
                        limitations=limitations,
                        tool_path=tool_path,
                    )

    async def _collect_node_async(self, inputs: StepExecutionInputs) -> NodeMetricsSnapshot:
        cluster = resolve_cluster(inputs.cluster)
        if _peer_prometheus_routing_unsupported(cluster, inputs.cluster):
            raise PeerMcpError("peer node Prometheus transport does not yet support multicluster prometheus routing")
        if not inputs.node_name:
            raise PeerMcpError("node peer transport requires node_name")

        target = TargetRef(namespace=None, kind="node", name=inputs.node_name)
        queries = node_metric_queries(inputs.node_name)

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tool_path = ["prometheus-mcp-server"]
                    limitations: list[str] = []
                    metrics: dict[str, Any] = {}
                    for label, query in queries.items():
                        raw = await self._call_tool(session, "execute_query", {"query": query})
                        tool_path.append("execute_query")
                        value = _normalize_metric_value(raw)
                        metrics[label] = value
                        if value is None:
                            limitations.append(f"metric unavailable: {label}")
                    metrics["prometheus_available"] = any(
                        value is not None for key, value in metrics.items() if key != "prometheus_available"
                    )
                    if not metrics["prometheus_available"]:
                        limitations.append("prometheus unavailable or returned no usable results")
                    return NodeMetricsSnapshot(
                        cluster_alias=cluster.alias,
                        target=target,
                        metrics=metrics,
                        limitations=limitations,
                        tool_path=tool_path,
                    )

    def collect_service_metrics(self, inputs: StepExecutionInputs) -> ServiceMetricsSnapshot:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running_loop = False
        else:
            running_loop = True

        if running_loop:
            result: dict[str, ServiceMetricsSnapshot] = {}
            error: dict[str, Exception] = {}

            def _runner() -> None:
                try:
                    result["snapshot"] = anyio.run(self._collect_service_async, inputs)
                except Exception as exc:  # pragma: no cover
                    error["exception"] = exc

            thread = threading.Thread(target=_runner, daemon=True)
            thread.start()
            thread.join()
            if "exception" in error:
                exc = error["exception"]
                if isinstance(exc, PeerMcpError):
                    raise exc
                raise PeerMcpError(_peer_error_message(exc)) from exc
            return result["snapshot"]

        try:
            return anyio.run(self._collect_service_async, inputs)
        except PeerMcpError:
            raise
        except Exception as exc:
            raise PeerMcpError(_peer_error_message(exc)) from exc

    def collect_service_range_metrics(
        self,
        inputs: StepExecutionInputs,
        *,
        max_metric_families: int = 1,
    ) -> ServiceMetricsSnapshot:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running_loop = False
        else:
            running_loop = True

        if running_loop:
            result: dict[str, ServiceMetricsSnapshot] = {}
            error: dict[str, Exception] = {}

            def _runner() -> None:
                try:
                    result["snapshot"] = anyio.run(
                        lambda: self._collect_service_range_async(
                            inputs,
                            max_metric_families=max_metric_families,
                        )
                    )
                except Exception as exc:  # pragma: no cover
                    error["exception"] = exc

            thread = threading.Thread(target=_runner, daemon=True)
            thread.start()
            thread.join()
            if "exception" in error:
                exc = error["exception"]
                if isinstance(exc, PeerMcpError):
                    raise exc
                raise PeerMcpError(_peer_error_message(exc)) from exc
            return result["snapshot"]

        try:
            return anyio.run(
                lambda: self._collect_service_range_async(
                    inputs,
                    max_metric_families=max_metric_families,
                )
            )
        except PeerMcpError:
            raise
        except Exception as exc:
            raise PeerMcpError(_peer_error_message(exc)) from exc

    def collect_node_metrics(self, inputs: StepExecutionInputs) -> NodeMetricsSnapshot:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running_loop = False
        else:
            running_loop = True

        if running_loop:
            result: dict[str, NodeMetricsSnapshot] = {}
            error: dict[str, Exception] = {}

            def _runner() -> None:
                try:
                    result["snapshot"] = anyio.run(self._collect_node_async, inputs)
                except Exception as exc:  # pragma: no cover
                    error["exception"] = exc

            thread = threading.Thread(target=_runner, daemon=True)
            thread.start()
            thread.join()
            if "exception" in error:
                exc = error["exception"]
                if isinstance(exc, PeerMcpError):
                    raise exc
                raise PeerMcpError(_peer_error_message(exc)) from exc
            return result["snapshot"]

        try:
            return anyio.run(self._collect_node_async, inputs)
        except PeerMcpError:
            raise
        except Exception as exc:
            raise PeerMcpError(_peer_error_message(exc)) from exc


class LokiMcpClient:
    def __init__(self, *, url: str | None = None, timeout_seconds: float | None = None):
        self.url = url if url is not None else get_loki_mcp_url()
        self.timeout_seconds = timeout_seconds or get_peer_mcp_timeout_seconds()

    def is_configured(self) -> bool:
        return bool(self.url)

    async def _call_tool(self, session: ClientSession, tool_name: str, arguments: dict[str, Any]) -> Any:
        result = await session.call_tool(tool_name, arguments)
        if getattr(result, "isError", False):
            raise PeerMcpError(f"{tool_name} returned MCP error")
        return _extract_content(result)

    async def _collect_workload_async(
        self,
        inputs: StepExecutionInputs,
        *,
        target: TargetRef,
        runtime_pod_name: str | None,
    ) -> LokiLogsSnapshot:
        if not self.url:
            raise PeerMcpError("loki peer transport is not configured")
        cluster = resolve_cluster(inputs.cluster)
        if _peer_loki_routing_unsupported(cluster, inputs.cluster):
            raise PeerMcpError("peer Loki transport does not yet support multicluster loki routing")
        if not target.namespace:
            raise PeerMcpError("workload Loki transport requires namespace")

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    raw = await self._call_tool(
                        session,
                        "loki_query",
                        {
                            "query": _build_workload_loki_query(target, runtime_pod_name or target.name),
                            "start": _format_loki_window(inputs.lookback_minutes),
                            "end": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "limit": get_log_tail_lines(),
                        },
                    )
                    return LokiLogsSnapshot(
                        cluster_alias=cluster.alias,
                        target=target,
                        log_excerpt=_normalize_loki_logs(raw),
                        tool_path=["loki-mcp-server", "loki_query"],
                    )

    async def _collect_service_async(
        self,
        inputs: StepExecutionInputs,
        *,
        target: TargetRef,
        object_state: dict[str, Any] | None = None,
    ) -> LokiLogsSnapshot:
        if not self.url:
            raise PeerMcpError("loki peer transport is not configured")
        cluster = resolve_cluster(inputs.cluster)
        if _peer_loki_routing_unsupported(cluster, inputs.cluster):
            raise PeerMcpError("peer Loki transport does not yet support multicluster loki routing")
        if not target.namespace or target.kind != "service":
            raise PeerMcpError("service Loki transport requires a service target with namespace")

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    raw: Any = None
                    for query in _build_service_loki_queries(target, object_state):
                        raw = await self._call_tool(
                            session,
                            "loki_query",
                            {
                                "query": query,
                                "start": _format_loki_window(inputs.lookback_minutes),
                                "end": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                "limit": get_log_tail_lines(),
                            },
                        )
                        normalized = _normalize_loki_logs(raw)
                        if normalized:
                            return LokiLogsSnapshot(
                                cluster_alias=cluster.alias,
                                target=target,
                                log_excerpt=normalized,
                                tool_path=["loki-mcp-server", "loki_query"],
                            )
                    return LokiLogsSnapshot(
                        cluster_alias=cluster.alias,
                        target=target,
                        log_excerpt=_normalize_loki_logs(raw),
                        tool_path=["loki-mcp-server", "loki_query"],
                    )

    def collect_workload_logs(
        self,
        inputs: StepExecutionInputs,
        *,
        target: TargetRef,
        runtime_pod_name: str | None,
    ) -> LokiLogsSnapshot:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running_loop = False
        else:
            running_loop = True

        if running_loop:
            result: dict[str, LokiLogsSnapshot] = {}
            error: dict[str, Exception] = {}

            def _runner() -> None:
                try:
                    result["snapshot"] = anyio.run(
                        lambda: self._collect_workload_async(
                            inputs,
                            target=target,
                            runtime_pod_name=runtime_pod_name,
                        )
                    )
                except Exception as exc:  # pragma: no cover
                    error["exception"] = exc

            thread = threading.Thread(target=_runner, daemon=True)
            thread.start()
            thread.join()
            if "exception" in error:
                exc = error["exception"]
                if isinstance(exc, PeerMcpError):
                    raise exc
                raise PeerMcpError(_peer_error_message(exc)) from exc
            return result["snapshot"]

        try:
            return anyio.run(
                lambda: self._collect_workload_async(
                    inputs,
                    target=target,
                    runtime_pod_name=runtime_pod_name,
                )
            )
        except PeerMcpError:
            raise
        except Exception as exc:
            raise PeerMcpError(_peer_error_message(exc)) from exc

    def collect_service_logs(
        self,
        inputs: StepExecutionInputs,
        *,
        target: TargetRef,
        object_state: dict[str, Any] | None = None,
    ) -> LokiLogsSnapshot:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running_loop = False
        else:
            running_loop = True

        if running_loop:
            result: dict[str, LokiLogsSnapshot] = {}
            error: dict[str, Exception] = {}

            def _runner() -> None:
                try:
                    result["snapshot"] = anyio.run(
                        lambda: self._collect_service_async(
                            inputs,
                            target=target,
                            object_state=object_state,
                        )
                    )
                except Exception as exc:  # pragma: no cover
                    error["exception"] = exc

            thread = threading.Thread(target=_runner, daemon=True)
            thread.start()
            thread.join()
            if "exception" in error:
                exc = error["exception"]
                if isinstance(exc, PeerMcpError):
                    raise exc
                raise PeerMcpError(_peer_error_message(exc)) from exc
            return result["snapshot"]

        try:
            return anyio.run(
                lambda: self._collect_service_async(
                    inputs,
                    target=target,
                    object_state=object_state,
                )
            )
        except PeerMcpError:
            raise
        except Exception as exc:
            raise PeerMcpError(_peer_error_message(exc)) from exc


class AlertmanagerMcpClient:
    def __init__(self, *, url: str | None = None, timeout_seconds: float | None = None):
        self.url = url if url is not None else get_alertmanager_mcp_url()
        self.timeout_seconds = timeout_seconds or get_peer_mcp_timeout_seconds()

    def is_configured(self) -> bool:
        return bool(self.url)

    async def _call_tool(self, session: ClientSession, tool_name: str, arguments: dict[str, Any]) -> Any:
        result = await session.call_tool(tool_name, arguments)
        if getattr(result, "isError", False):
            raise PeerMcpError(f"{tool_name} returned MCP error")
        return _extract_content(result)

    async def _collect_async(self, inputs: StepExecutionInputs) -> AlertmanagerAlertSnapshot:
        if not self.url:
            raise PeerMcpError("alertmanager peer transport is not configured")
        cluster = resolve_cluster(inputs.cluster, labels=inputs.labels)
        if _peer_alertmanager_routing_unsupported(cluster, inputs.cluster):
            raise PeerMcpError("peer Alertmanager transport does not yet support multicluster alertmanager routing")
        filters = _alert_identity_filters(inputs)

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as http_client:
            async with streamable_http_client(self.url, http_client=http_client) as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    raw = await self._call_tool(
                        session,
                        "alertmanager_list_alerts",
                        {
                            "labelFilters": filters,
                            "active": True,
                            "silenced": False,
                            "inhibited": False,
                            "unprocessed": False,
                        },
                    )
                    return AlertmanagerAlertSnapshot(
                        cluster_alias=cluster.alias,
                        alerts=_normalize_alertmanager_alerts(raw),
                        tool_path=["alertmanager-mcp-server", "alertmanager_list_alerts"],
                    )

    def collect_alert_state(self, inputs: StepExecutionInputs) -> AlertmanagerAlertSnapshot:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running_loop = False
        else:
            running_loop = True

        if running_loop:
            result: dict[str, AlertmanagerAlertSnapshot] = {}
            error: dict[str, Exception] = {}

            def _runner() -> None:
                try:
                    result["snapshot"] = anyio.run(lambda: self._collect_async(inputs))
                except Exception as exc:  # pragma: no cover
                    error["exception"] = exc

            thread = threading.Thread(target=_runner, daemon=True)
            thread.start()
            thread.join()
            if "exception" in error:
                exc = error["exception"]
                if isinstance(exc, PeerMcpError):
                    raise exc
                raise PeerMcpError(_peer_error_message(exc)) from exc
            return result["snapshot"]

        try:
            return anyio.run(lambda: self._collect_async(inputs))
        except PeerMcpError:
            raise
        except Exception as exc:
            raise PeerMcpError(_peer_error_message(exc)) from exc
