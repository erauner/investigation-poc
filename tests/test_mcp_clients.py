from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from investigation_orchestrator import mcp_clients
from investigation_orchestrator.mcp_clients import AlertmanagerMcpClient, KubernetesMcpClient, LokiMcpClient, PrometheusMcpClient
from investigation_service.models import StepExecutionInputs
import pytest
import re


class _DummyAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _DummySession:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def initialize(self) -> None:
        return None


@asynccontextmanager
async def _dummy_streamable_http_client(*args, **kwargs):
    yield object(), object(), lambda: "session-id"


def test_collect_service_range_metrics_parses_execute_range_query_payload(monkeypatch) -> None:
    client = PrometheusMcpClient()
    calls: list[tuple[str, dict[str, object]]] = []

    async def _call_tool(_self, _session, tool_name: str, args: dict[str, object]):
        calls.append((tool_name, args))
        if tool_name != "execute_range_query":
            raise AssertionError(f"unexpected tool {tool_name}")
        query = str(args["query"])
        if "status=~" in query:
            return {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"__name__": "http_server_request_duration_seconds_count"},
                            "values": [[1710000000, "0"], [1710000060, "0.5"]],
                        }
                    ],
                },
            }
        if "histogram_quantile" in query:
            return {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"__name__": "http_server_request_duration_seconds_bucket"},
                            "values": [[1710000000, "0.9"], [1710000060, "1.2"]],
                        }
                    ],
                },
            }
        return {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {"__name__": "http_server_request_duration_seconds_count"},
                        "values": [[1710000000, "10.0"], [1710000060, "12.5"]],
                    }
                ],
            },
        }

    monkeypatch.setattr(mcp_clients.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(mcp_clients, "streamable_http_client", _dummy_streamable_http_client)
    monkeypatch.setattr(mcp_clients, "ClientSession", _DummySession)
    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster: SimpleNamespace(alias="local-kind", prometheus_url=None),
    )
    monkeypatch.setattr(PrometheusMcpClient, "_call_tool", _call_tool)

    snapshot = client.collect_service_range_metrics(
        StepExecutionInputs(
            request_kind="service_context",
            namespace="operator-smoke",
            target="service/api",
            profile="service",
            service_name="api",
            lookback_minutes=15,
        ),
        max_metric_families=1,
    )

    assert len(calls) == 3
    assert all(tool_name == "execute_range_query" for tool_name, _args in calls)
    assert all(isinstance(args["start"], str) and args["start"] for _tool_name, args in calls)
    assert all(isinstance(args["end"], str) and args["end"] for _tool_name, args in calls)
    assert all(args["step"] == "60s" for _tool_name, args in calls)
    assert snapshot.metrics["service_request_rate"] == 12.5
    assert snapshot.metrics["service_error_rate"] == 0.5
    assert snapshot.metrics["service_latency_p95_seconds"] == 1.2
    assert snapshot.metrics["prometheus_available"] is True
    assert snapshot.limitations == []


def test_collect_service_range_metrics_uses_shared_window_and_ignores_non_finite_values(monkeypatch) -> None:
    client = PrometheusMcpClient()
    calls: list[tuple[str, dict[str, object]]] = []

    async def _call_tool(_self, _session, tool_name: str, args: dict[str, object]):
        calls.append((tool_name, args))
        if tool_name != "execute_range_query":
            raise AssertionError(f"unexpected tool {tool_name}")
        query = str(args["query"])
        if "status=~" in query:
            return {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"__name__": "http_server_request_duration_seconds_count"},
                            "values": [[1710000000, "NaN"], [1710000060, "Inf"]],
                        }
                    ],
                },
            }
        if "histogram_quantile" in query:
            return {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"__name__": "http_server_request_duration_seconds_bucket"},
                            "values": [[1710000000, "1.5"], [1710000060, "1.8"]],
                        }
                    ],
                },
            }
        return {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {"__name__": "http_server_request_duration_seconds_count"},
                        "values": [[1710000000, "8.0"], [1710000060, "10.0"]],
                    }
                ],
            },
        }

    monkeypatch.setattr(mcp_clients.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(mcp_clients, "streamable_http_client", _dummy_streamable_http_client)
    monkeypatch.setattr(mcp_clients, "ClientSession", _DummySession)
    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster: SimpleNamespace(alias="local-kind", prometheus_url=None),
    )
    monkeypatch.setattr(PrometheusMcpClient, "_call_tool", _call_tool)

    snapshot = client.collect_service_range_metrics(
        StepExecutionInputs(
            request_kind="service_context",
            namespace="operator-smoke",
            target="service/api",
            profile="service",
            service_name="api",
            lookback_minutes=1,
        ),
        max_metric_families=2,
    )

    starts = {str(args["start"]) for _tool_name, args in calls}
    ends = {str(args["end"]) for _tool_name, args in calls}
    assert starts and len(starts) == 1
    assert ends and len(ends) == 1
    assert snapshot.metrics["service_request_rate"] == 10.0
    assert snapshot.metrics["service_error_rate"] is None
    assert snapshot.metrics["service_latency_p95_seconds"] == 1.8
    assert snapshot.metrics["prometheus_available"] is True
    assert "metric unavailable: service_error_rate" in snapshot.limitations


def test_collect_node_top_pods_parses_resources_list_payload(monkeypatch) -> None:
    client = KubernetesMcpClient()
    calls: list[tuple[str, dict[str, object]]] = []

    async def _call_tool(_self, _session, tool_name: str, args: dict[str, object]):
        calls.append((tool_name, args))
        if tool_name != "resources_list":
            raise AssertionError(f"unexpected tool {tool_name}")
        return {
            "items": [
                {
                    "metadata": {"namespace": "operator-smoke", "name": "worker-helper"},
                    "spec": {"containers": [{"resources": {"requests": {"memory": "128Mi"}}}]},
                },
                {
                    "metadata": {"namespace": "operator-smoke", "name": "api-0"},
                    "spec": {"containers": [{"resources": {"requests": {"memory": "512Mi"}}}]},
                },
                {
                    "metadata": {"namespace": "operator-smoke", "name": "api-1"},
                    "spec": {"containers": [{"resources": {"requests": {"memory": "256Mi"}}}]},
                },
            ]
        }

    monkeypatch.setattr(mcp_clients.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(mcp_clients, "streamable_http_client", _dummy_streamable_http_client)
    monkeypatch.setattr(mcp_clients, "ClientSession", _DummySession)
    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster: SimpleNamespace(alias="local-kind", kube_context=None, kubeconfig_path=None),
    )
    monkeypatch.setattr(KubernetesMcpClient, "_call_tool", _call_tool)

    snapshot = client.collect_node_top_pods(
        StepExecutionInputs(
            request_kind="target_context",
            target="node/worker3",
            profile="workload",
            node_name="worker3",
            lookback_minutes=15,
        ),
        limit=2,
    )

    assert calls == [
        (
            "resources_list",
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "fieldSelector": "spec.nodeName=worker3",
            },
        )
    ]
    assert snapshot.top_pods_by_memory_request == [
        {"namespace": "operator-smoke", "name": "api-0", "memory_request_bytes": 536870912},
        {"namespace": "operator-smoke", "name": "api-1", "memory_request_bytes": 268435456},
    ]


def test_collect_service_runtime_parses_yaml_service_payload_for_topology(monkeypatch) -> None:
    client = KubernetesMcpClient(url="http://kubernetes-mcp.example/mcp")
    calls: list[tuple[str, dict[str, object]]] = []

    async def _call_tool(_self, _session, tool_name: str, args: dict[str, object]):
        calls.append((tool_name, args))
        if tool_name == "resources_get":
            return """
apiVersion: v1
kind: Service
metadata:
  name: envoy-proxy-metrics
  namespace: envoy-gateway-system
spec:
  selector:
    app.kubernetes.io/component: proxy
    app.kubernetes.io/managed-by: envoy-gateway
    app.kubernetes.io/name: envoy
"""
        if tool_name == "pods_list_in_namespace":
            return """
- apiVersion: v1
  kind: Pod
  metadata:
    name: envoy-public-abc123
    labels:
      app.kubernetes.io/component: proxy
      app.kubernetes.io/managed-by: envoy-gateway
      app.kubernetes.io/name: envoy
    ownerReferences:
      - kind: ReplicaSet
        name: envoy-public-6d4c8b7f5
  status:
    containerStatuses:
      - ready: true
        restartCount: 0
"""
        if tool_name == "events_list":
            return {"items": []}
        raise AssertionError(f"unexpected tool {tool_name}")

    monkeypatch.setattr(mcp_clients.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(mcp_clients, "streamable_http_client", _dummy_streamable_http_client)
    monkeypatch.setattr(mcp_clients, "ClientSession", _DummySession)
    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster: SimpleNamespace(alias="local-kind", kube_context=None, kubeconfig_path=None),
    )
    monkeypatch.setattr(KubernetesMcpClient, "_call_tool", _call_tool)

    snapshot = client.collect_service_runtime(
        StepExecutionInputs(
            request_kind="service_context",
            namespace="envoy-gateway-system",
            target="service/envoy-proxy-metrics",
            profile="service",
            service_name="envoy-proxy-metrics",
            lookback_minutes=15,
        )
    )

    assert [tool_name for tool_name, _args in calls] == ["resources_get", "pods_list_in_namespace", "events_list"]
    assert snapshot.object_state["selector"] == {
        "app.kubernetes.io/component": "proxy",
        "app.kubernetes.io/managed-by": "envoy-gateway",
        "app.kubernetes.io/name": "envoy",
    }
    assert snapshot.object_state["matchedPodCount"] == 1
    assert snapshot.object_state["matchedPods"] == [
        {
            "name": "envoy-public-abc123",
            "phase": None,
            "ready": True,
            "restartCount": 0,
            "workload": {"kind": "deployment", "name": "envoy-public"},
        }
    ]


def test_collect_workload_logs_normalizes_loki_payload(monkeypatch) -> None:
    client = LokiMcpClient(url="http://loki-mcp.example/mcp")
    calls: list[tuple[str, dict[str, object]]] = []

    async def _call_tool(_self, _session, tool_name: str, args: dict[str, object]):
        calls.append((tool_name, args))
        if tool_name != "loki_query":
            raise AssertionError(f"unexpected tool {tool_name}")
        return {
            "entries": [
                {"message": "error: upstream failed"},
                {"line": "exception: retry exhausted"},
            ]
        }

    monkeypatch.setattr(mcp_clients.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(mcp_clients, "streamable_http_client", _dummy_streamable_http_client)
    monkeypatch.setattr(mcp_clients, "ClientSession", _DummySession)
    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster: SimpleNamespace(alias="local-kind", loki_url=None),
    )
    monkeypatch.setattr(LokiMcpClient, "_call_tool", _call_tool)

    snapshot = client.collect_workload_logs(
        StepExecutionInputs(
            request_kind="target_context",
            namespace="operator-smoke",
            target="deployment/api",
            profile="workload",
            lookback_minutes=15,
        ),
        target=mcp_clients.TargetRef(namespace="operator-smoke", kind="deployment", name="api"),
        runtime_pod_name="api-abc123",
    )

    assert calls == [
        (
            "loki_query",
            calls[0][1],
        )
    ]
    assert calls[0][1]["query"] == '{namespace="operator-smoke",pod="api-abc123"}'
    assert calls[0][1]["limit"] == 200
    assert re.fullmatch(r".+Z", str(calls[0][1]["start"]))
    assert re.fullmatch(r".+Z", str(calls[0][1]["end"]))
    assert snapshot.log_excerpt == "error: upstream failed\nexception: retry exhausted"
    assert snapshot.tool_path == ["loki-mcp-server", "loki_query"]


def test_collect_service_logs_builds_loki_query_from_matched_pods(monkeypatch) -> None:
    client = LokiMcpClient(url="http://loki-mcp.example/mcp")
    calls: list[tuple[str, dict[str, object]]] = []

    async def _call_tool(_self, _session, tool_name: str, args: dict[str, object]):
        calls.append((tool_name, args))
        if tool_name != "loki_query":
            raise AssertionError(f"unexpected tool {tool_name}")
        return {"data": {"result": [{"values": [["1", "error: upstream returned 500"]]}]}}

    monkeypatch.setattr(mcp_clients.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(mcp_clients, "streamable_http_client", _dummy_streamable_http_client)
    monkeypatch.setattr(mcp_clients, "ClientSession", _DummySession)
    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster: SimpleNamespace(alias="local-kind", loki_url=None),
    )
    monkeypatch.setattr(LokiMcpClient, "_call_tool", _call_tool)

    snapshot = client.collect_service_logs(
        StepExecutionInputs(
            request_kind="service_context",
            namespace="operator-smoke",
            target="service/api",
            profile="service",
            service_name="api",
            lookback_minutes=15,
        ),
        target=mcp_clients.TargetRef(namespace="operator-smoke", kind="service", name="api"),
        object_state={
            "kind": "service",
            "name": "api",
            "matchedPods": [
                {"name": "api-abc123"},
                {"name": "api-def456"},
                {"name": "api-def456"},
            ],
        },
    )

    assert calls == [
        (
            "loki_query",
            calls[0][1],
        )
    ]
    assert calls[0][1]["query"] == '{namespace="operator-smoke",pod=~"^(api-abc123|api-def456)$"}'
    assert calls[0][1]["limit"] == 200
    assert re.fullmatch(r".+Z", str(calls[0][1]["start"]))
    assert re.fullmatch(r".+Z", str(calls[0][1]["end"]))
    assert snapshot.log_excerpt == "error: upstream returned 500"
    assert snapshot.tool_path == ["loki-mcp-server", "loki_query"]


def test_collect_service_logs_falls_back_to_selector_app_query_when_pod_query_is_empty(monkeypatch) -> None:
    client = LokiMcpClient(url="http://loki-mcp.example/mcp")
    calls: list[tuple[str, dict[str, object]]] = []

    async def _call_tool(_self, _session, tool_name: str, args: dict[str, object]):
        calls.append((tool_name, args))
        if tool_name != "loki_query":
            raise AssertionError(f"unexpected tool {tool_name}")
        if args["query"] == '{namespace="operator-smoke",pod=~"^(api-abc123)$"}':
            return {"data": {"result": []}}
        if args["query"] == '{namespace="operator-smoke",app="api"}':
            return {"data": {"result": [{"values": [["1", "error: upstream returned 500"]]}]}}
        raise AssertionError(f"unexpected query {args['query']}")

    monkeypatch.setattr(mcp_clients.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(mcp_clients, "streamable_http_client", _dummy_streamable_http_client)
    monkeypatch.setattr(mcp_clients, "ClientSession", _DummySession)
    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster: SimpleNamespace(alias="local-kind", loki_url=None),
    )
    monkeypatch.setattr(LokiMcpClient, "_call_tool", _call_tool)

    snapshot = client.collect_service_logs(
        StepExecutionInputs(
            request_kind="service_context",
            namespace="operator-smoke",
            target="service/api",
            profile="service",
            service_name="api",
            lookback_minutes=15,
        ),
        target=mcp_clients.TargetRef(namespace="operator-smoke", kind="service", name="api"),
        object_state={
            "kind": "service",
            "name": "api",
            "selector": {"app.kubernetes.io/name": "api"},
            "matchedPods": [{"name": "api-abc123"}],
        },
    )

    assert [args["query"] for _tool_name, args in calls] == [
        '{namespace="operator-smoke",pod=~"^(api-abc123)$"}',
        '{namespace="operator-smoke",app="api"}',
    ]
    assert snapshot.log_excerpt == "error: upstream returned 500"
    assert snapshot.tool_path == ["loki-mcp-server", "loki_query"]


def test_collect_service_logs_does_not_fall_back_to_unmapped_selector_labels(monkeypatch) -> None:
    client = LokiMcpClient(url="http://loki-mcp.example/mcp")
    calls: list[tuple[str, dict[str, object]]] = []

    async def _call_tool(_self, _session, tool_name: str, args: dict[str, object]):
        calls.append((tool_name, args))
        return {"data": {"result": []}}

    monkeypatch.setattr(mcp_clients.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(mcp_clients, "streamable_http_client", _dummy_streamable_http_client)
    monkeypatch.setattr(mcp_clients, "ClientSession", _DummySession)
    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster: SimpleNamespace(alias="local-kind", loki_url=None),
    )
    monkeypatch.setattr(LokiMcpClient, "_call_tool", _call_tool)

    snapshot = client.collect_service_logs(
        StepExecutionInputs(
            request_kind="service_context",
            namespace="operator-smoke",
            target="service/api",
            profile="service",
            service_name="api",
            lookback_minutes=15,
        ),
        target=mcp_clients.TargetRef(namespace="operator-smoke", kind="service", name="api"),
        object_state={
            "kind": "service",
            "name": "api",
            "selector": {"app": "api", "k8s-app": "api"},
        },
    )

    assert calls == []
    assert snapshot.log_excerpt == ""


def test_collect_service_logs_does_not_fall_back_to_app_query_without_emitted_selector(monkeypatch) -> None:
    client = LokiMcpClient(url="http://loki-mcp.example/mcp")
    calls: list[tuple[str, dict[str, object]]] = []

    async def _call_tool(_self, _session, tool_name: str, args: dict[str, object]):
        calls.append((tool_name, args))
        return {"data": {"result": []}}

    monkeypatch.setattr(mcp_clients.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(mcp_clients, "streamable_http_client", _dummy_streamable_http_client)
    monkeypatch.setattr(mcp_clients, "ClientSession", _DummySession)
    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster: SimpleNamespace(alias="local-kind", loki_url=None),
    )
    monkeypatch.setattr(LokiMcpClient, "_call_tool", _call_tool)

    snapshot = client.collect_service_logs(
        StepExecutionInputs(
            request_kind="service_context",
            namespace="operator-smoke",
            target="service/api",
            profile="service",
            service_name="api",
            lookback_minutes=15,
        ),
        target=mcp_clients.TargetRef(namespace="operator-smoke", kind="service", name="api"),
        object_state={"kind": "service", "name": "api"},
    )

    assert calls == []
    assert snapshot.log_excerpt == ""


def test_collect_service_logs_rejects_remote_loki_routing_without_explicit_cluster(monkeypatch) -> None:
    client = LokiMcpClient(url="http://loki-mcp.example/mcp")

    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster: SimpleNamespace(alias="remote-a", loki_url="http://remote-loki:3100"),
    )
    monkeypatch.setattr(mcp_clients, "get_default_cluster_alias", lambda: "local-kind")
    monkeypatch.setattr(mcp_clients, "get_cluster_name", lambda: "local-kind")
    monkeypatch.setattr(mcp_clients, "get_loki_url", lambda: "http://local-loki:3100")

    with pytest.raises(mcp_clients.PeerMcpError, match="multicluster loki routing"):
        client.collect_service_logs(
            StepExecutionInputs(
                request_kind="service_context",
                namespace="operator-smoke",
                target="service/api",
                profile="service",
                service_name="api",
                lookback_minutes=15,
            ),
            target=mcp_clients.TargetRef(namespace="operator-smoke", kind="service", name="api"),
        )


def test_collect_service_logs_rejects_remote_loki_routing_when_remote_cluster_has_no_loki_url(monkeypatch) -> None:
    client = LokiMcpClient(url="http://loki-mcp.example/mcp")

    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster: SimpleNamespace(alias="remote-a", loki_url=None),
    )
    monkeypatch.setattr(mcp_clients, "get_default_cluster_alias", lambda: "local-kind")
    monkeypatch.setattr(mcp_clients, "get_cluster_name", lambda: "local-kind")
    monkeypatch.setattr(mcp_clients, "get_loki_url", lambda: "http://local-loki:3100")

    with pytest.raises(mcp_clients.PeerMcpError, match="multicluster loki routing"):
        client.collect_service_logs(
            StepExecutionInputs(
                request_kind="service_context",
                namespace="operator-smoke",
                target="service/api",
                profile="service",
                service_name="api",
                cluster="remote-a",
                lookback_minutes=15,
            ),
            target=mcp_clients.TargetRef(namespace="operator-smoke", kind="service", name="api"),
        )


def test_collect_alert_state_builds_alertmanager_filters_and_normalizes_payload(monkeypatch) -> None:
    client = AlertmanagerMcpClient(url="http://alertmanager-mcp.example/mcp")
    calls: list[tuple[str, dict[str, object]]] = []

    async def _call_tool(_self, _session, tool_name: str, args: dict[str, object]):
        calls.append((tool_name, args))
        if tool_name != "alertmanager_list_alerts":
            raise AssertionError(f"unexpected tool {tool_name}")
        return {
            "alerts": [
                {
                    "fingerprint": "abc",
                    "status": {"state": "active"},
                    "labels": {"alertname": "PodCrashLooping", "namespace": "operator-smoke", "pod": "crashy"},
                    "annotations": {"summary": "CrashLoop"},
                    "startsAt": "2026-03-11T10:00:00Z",
                    "endsAt": "2026-03-11T11:00:00Z",
                }
            ]
        }

    monkeypatch.setattr(mcp_clients.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(mcp_clients, "streamable_http_client", _dummy_streamable_http_client)
    monkeypatch.setattr(mcp_clients, "ClientSession", _DummySession)
    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster, labels=None: SimpleNamespace(alias="local-kind", alertmanager_url=None),
    )
    monkeypatch.setattr(AlertmanagerMcpClient, "_call_tool", _call_tool)

    snapshot = client.collect_alert_state(
        StepExecutionInputs(
            request_kind="alert_context",
            namespace="operator-smoke",
            target="pod/crashy",
            profile="workload",
            alertname="PodCrashLooping",
            labels={"pod": "crashy"},
        )
    )

    assert calls == [
        (
            "alertmanager_list_alerts",
            {
                "labelFilters": {"alertname": "PodCrashLooping", "pod": "crashy", "namespace": "operator-smoke"},
                "active": True,
                "silenced": False,
                "inhibited": False,
                "unprocessed": False,
            },
        )
    ]
    assert snapshot.alerts[0]["fingerprint"] == "abc"
    assert snapshot.tool_path == ["alertmanager-mcp-server", "alertmanager_list_alerts"]


def test_collect_alert_state_uses_explicit_target_as_identity_fallback(monkeypatch) -> None:
    client = AlertmanagerMcpClient(url="http://alertmanager-mcp.example/mcp")
    calls: list[tuple[str, dict[str, object]]] = []

    async def _call_tool(_self, _session, tool_name: str, args: dict[str, object]):
        calls.append((tool_name, args))
        return {"alerts": []}

    monkeypatch.setattr(mcp_clients.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(mcp_clients, "streamable_http_client", _dummy_streamable_http_client)
    monkeypatch.setattr(mcp_clients, "ClientSession", _DummySession)
    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster, labels=None: SimpleNamespace(alias="local-kind", alertmanager_url=None),
    )
    monkeypatch.setattr(AlertmanagerMcpClient, "_call_tool", _call_tool)

    client.collect_alert_state(
        StepExecutionInputs(
            request_kind="alert_context",
            namespace="operator-smoke",
            target="deployment/crashy",
            profile="workload",
            alertname="PodCrashLooping",
            labels={},
        )
    )

    assert calls == [
        (
            "alertmanager_list_alerts",
            {
                "labelFilters": {
                    "alertname": "PodCrashLooping",
                    "namespace": "operator-smoke",
                    "deployment": "crashy",
                },
                "active": True,
                "silenced": False,
                "inhibited": False,
                "unprocessed": False,
            },
        )
    ]


def test_collect_alert_state_does_not_duplicate_alias_identity_with_explicit_target(monkeypatch) -> None:
    client = AlertmanagerMcpClient(url="http://alertmanager-mcp.example/mcp")
    calls: list[tuple[str, dict[str, object]]] = []

    async def _call_tool(_self, _session, tool_name: str, args: dict[str, object]):
        calls.append((tool_name, args))
        return {"alerts": []}

    monkeypatch.setattr(mcp_clients.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(mcp_clients, "streamable_http_client", _dummy_streamable_http_client)
    monkeypatch.setattr(mcp_clients, "ClientSession", _DummySession)
    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster, labels=None: SimpleNamespace(alias="local-kind", alertmanager_url=None),
    )
    monkeypatch.setattr(AlertmanagerMcpClient, "_call_tool", _call_tool)

    client.collect_alert_state(
        StepExecutionInputs(
            request_kind="alert_context",
            namespace="operator-smoke",
            target="pod/crashy",
            profile="workload",
            alertname="PodCrashLooping",
            labels={"pod_name": "crashy"},
        )
    )

    assert calls == [
        (
            "alertmanager_list_alerts",
            {
                "labelFilters": {
                    "alertname": "PodCrashLooping",
                    "namespace": "operator-smoke",
                    "pod_name": "crashy",
                },
                "active": True,
                "silenced": False,
                "inhibited": False,
                "unprocessed": False,
            },
        )
    ]


def test_collect_alert_state_uses_structured_service_name_when_target_is_absent(monkeypatch) -> None:
    client = AlertmanagerMcpClient(url="http://alertmanager-mcp.example/mcp")
    calls: list[tuple[str, dict[str, object]]] = []

    async def _call_tool(_self, _session, tool_name: str, args: dict[str, object]):
        calls.append((tool_name, args))
        return {"alerts": []}

    monkeypatch.setattr(mcp_clients.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(mcp_clients, "streamable_http_client", _dummy_streamable_http_client)
    monkeypatch.setattr(mcp_clients, "ClientSession", _DummySession)
    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster, labels=None: SimpleNamespace(alias="local-kind", alertmanager_url=None),
    )
    monkeypatch.setattr(AlertmanagerMcpClient, "_call_tool", _call_tool)

    client.collect_alert_state(
        StepExecutionInputs(
            request_kind="alert_context",
            namespace="operator-smoke",
            profile="service",
            alertname="EnvoyHighErrorRate",
            service_name="api",
            labels={},
        )
    )

    assert calls == [
        (
            "alertmanager_list_alerts",
            {
                "labelFilters": {
                    "alertname": "EnvoyHighErrorRate",
                    "namespace": "operator-smoke",
                    "service": "api",
                },
                "active": True,
                "silenced": False,
                "inhibited": False,
                "unprocessed": False,
            },
        )
    ]


def test_collect_alert_state_rejects_remote_alertmanager_routing(monkeypatch) -> None:
    client = AlertmanagerMcpClient(url="http://alertmanager-mcp.example/mcp")

    monkeypatch.setattr(
        mcp_clients,
        "resolve_cluster",
        lambda _cluster, labels=None: SimpleNamespace(alias="remote-a", alertmanager_url="http://remote-alertmanager:9093"),
    )
    monkeypatch.setattr(mcp_clients, "get_default_cluster_alias", lambda: "local-kind")
    monkeypatch.setattr(mcp_clients, "get_cluster_name", lambda: "local-kind")
    monkeypatch.setattr(mcp_clients, "get_alertmanager_url", lambda: "http://local-alertmanager:9093")

    with pytest.raises(mcp_clients.PeerMcpError, match="multicluster alertmanager routing"):
        client.collect_alert_state(
            StepExecutionInputs(
                request_kind="alert_context",
                namespace="operator-smoke",
                alertname="PodCrashLooping",
                labels={"pod": "crashy"},
            )
        )
