from investigation_service.models import BuildInvestigationPlanRequest, TargetRef
from investigation_service.k8s_adapter import normalize_k8s_object_payload
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


def test_normalized_peer_pod_payload_preserves_operator_and_restart_semantics() -> None:
    raw_pod = {
        "metadata": {
            "name": "crashy-abc123",
            "namespace": "operator-smoke",
            "creationTimestamp": "2026-03-09T12:00:00Z",
            "labels": {
                "app.kubernetes.io/managed-by": "homelab-operator",
            },
            "ownerReferences": [
                {"kind": "Backend", "name": "crashy"},
            ],
        },
        "spec": {
            "containers": [
                {
                    "name": "app",
                    "image": "busybox:1.36",
                    "command": ["sh"],
                    "args": ["-c", "exit 1"],
                }
            ]
        },
        "status": {
            "phase": "Running",
            "reason": "CrashLoopBackOff",
            "conditions": [{"type": "Ready", "status": "False"}],
            "containerStatuses": [
                {
                    "name": "app",
                    "ready": False,
                    "restartCount": 5,
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    "lastState": {"terminated": {"reason": "Error", "exitCode": 1}},
                }
            ],
        },
    }

    normalized = normalize_k8s_object_payload(
        raw_pod,
        TargetRef(namespace="operator-smoke", kind="pod", name="crashy-abc123"),
    )
    bundle = materialize_workload_evidence(
        BuildInvestigationPlanRequest(
            cluster=None,
            namespace="operator-smoke",
            target="pod/crashy-abc123",
            profile="workload",
            lookback_minutes=15,
        ),
        target=TargetRef(namespace="operator-smoke", kind="pod", name="crashy-abc123"),
        object_state=normalized,
        events=["Warning BackOff pod/crashy-abc123"],
        log_excerpt="panic: startup failed",
        cluster_alias="erauner-home",
    )

    titles = {finding.title for finding in bundle.findings}
    assert "Crash Loop Detected" in titles
    assert any("operator-managed workload" in hint for hint in bundle.enrichment_hints)
