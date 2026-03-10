from investigation_service.models import BuildInvestigationPlanRequest, TargetRef
from investigation_service.tools import materialize_workload_evidence


def test_materialize_workload_evidence_preserves_service_owned_semantics() -> None:
    bundle = materialize_workload_evidence(
        BuildInvestigationPlanRequest(
            cluster=None,
            namespace="operator-smoke",
            target="pod/crashy-abc123",
            profile="workload",
            lookback_minutes=15,
        ),
        target=TargetRef(namespace="operator-smoke", kind="pod", name="crashy-abc123"),
        object_state={
            "kind": "pod",
            "name": "crashy-abc123",
            "phase": "Running",
            "containers": [{"name": "app", "restartCount": 5, "ready": False}],
        },
        events=["Warning BackOff pod/crashy-abc123"],
        log_excerpt="panic: startup failed",
        cluster_alias="erauner-home",
        extra_limitations=["peer logs truncated"],
    )

    titles = {finding.title for finding in bundle.findings}
    assert "Crash Loop Detected" in titles
    assert bundle.cluster == "erauner-home"
    assert bundle.target.name == "crashy-abc123"
    assert "peer logs truncated" in bundle.limitations
