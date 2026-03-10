from dataclasses import dataclass, field
from typing import Any
import asyncio
import json
import threading

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from investigation_service.cluster_registry import resolve_cluster
from investigation_service.k8s_adapter import (
    normalize_k8s_object_payload,
    resolve_runtime_target,
    resolve_target,
)
from investigation_service.models import StepExecutionInputs, TargetRef
from investigation_service.settings import (
    get_cluster_name,
    get_default_cluster_alias,
    get_kubernetes_mcp_url,
    get_peer_mcp_timeout_seconds,
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


def _pick_runtime_pod_for_deployment(deployment_raw: Any, pods_raw: Any) -> str | None:
    if not isinstance(deployment_raw, dict):
        return None
    if isinstance(pods_raw, dict):
        items = pods_raw.get("pods") or pods_raw.get("items") or []
    elif isinstance(pods_raw, list):
        items = pods_raw
    else:
        items = []

    deployment_meta = deployment_raw.get("metadata", {}) or {}
    deployment_name = deployment_meta.get("name")
    selector = deployment_raw.get("spec", {}).get("selector", {}).get("matchLabels", {}) or {}
    if not selector:
        return None
    selector_items = set(selector.items())

    def _matches_selector(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        labels = item.get("metadata", {}).get("labels", {}) or {}
        if selector_items and not selector_items.issubset(set(labels.items())):
            return False
        return True

    def _score(item: dict) -> tuple[int, str]:
        owner_refs = item.get("metadata", {}).get("ownerReferences", []) or []
        has_owner_hint = any(
            owner.get("kind") == "ReplicaSet"
            and deployment_name
            and str(owner.get("name", "")).startswith(f"{deployment_name}-")
            for owner in owner_refs
        )
        return (1 if has_owner_hint else 0, item.get("metadata", {}).get("creationTimestamp", "") or "")

    candidates = [item for item in items if _matches_selector(item)]
    if not candidates:
        return None
    candidates.sort(key=_score, reverse=True)
    return _extract_name(candidates[0])


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

    async def _collect_async(self, inputs: StepExecutionInputs) -> WorkloadRuntimeSnapshot:
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
                            pod_name = _pick_runtime_pod_for_deployment(raw_object_state, pods_raw)
                            if not pod_name:
                                raise PeerMcpError("could not resolve runtime pod for deployment target")
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
                    )

    def collect_workload_runtime(self, inputs: StepExecutionInputs) -> WorkloadRuntimeSnapshot:
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
                    result["snapshot"] = anyio.run(self._collect_async, inputs)
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
            return anyio.run(self._collect_async, inputs)
        except PeerMcpError:
            raise
        except Exception as exc:
            raise PeerMcpError(_peer_error_message(exc)) from exc
