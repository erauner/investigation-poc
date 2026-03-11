from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from investigation_orchestrator import mcp_clients
from investigation_orchestrator.mcp_clients import KubernetesMcpClient, PrometheusMcpClient
from investigation_service.models import StepExecutionInputs


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
    assert snapshot.limitations == []
