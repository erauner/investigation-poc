from investigation_service import tools
from investigation_service.models import (
    FindUnhealthyPodRequest,
    TargetRef,
    UnhealthyWorkloadCandidate,
    UnhealthyWorkloadsResponse,
)


def test_find_unhealthy_pod_accepts_explicit_current_context_in_legacy_mode(monkeypatch) -> None:
    monkeypatch.delenv("CLUSTER_REGISTRY_PATH", raising=False)
    monkeypatch.delenv("DEFAULT_CLUSTER_ALIAS", raising=False)
    monkeypatch.delenv("CLUSTER_NAME", raising=False)
    captured = {}

    def fake_find_unhealthy_workloads(namespace: str, limit: int, cluster=None):
        captured["cluster_alias"] = cluster.alias
        captured["cluster_source"] = cluster.source
        return UnhealthyWorkloadsResponse(
            cluster=cluster.alias,
            namespace=namespace,
            candidates=[
                UnhealthyWorkloadCandidate(
                    target="pod/crashy-abc123",
                    namespace=namespace,
                    kind="pod",
                    name="crashy-abc123",
                    phase="CrashLoopBackOff",
                    reason="CrashLoopBackOff",
                    restart_count=5,
                    ready=False,
                    summary="pod is restarting repeatedly",
                )
            ],
            limitations=[],
        )

    monkeypatch.setattr(tools, "find_unhealthy_workloads_impl", fake_find_unhealthy_workloads)

    response = tools.find_unhealthy_pod(FindUnhealthyPodRequest(cluster="current-context", namespace="kagent-smoke"))

    assert captured == {
        "cluster_alias": "current-context",
        "cluster_source": "legacy_current_context",
    }
    assert response.cluster == "current-context"
    assert response.candidate is not None
    assert response.candidate.target == "pod/crashy-abc123"
