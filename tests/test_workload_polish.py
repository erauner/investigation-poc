from investigation_service.analysis import derive_findings
from investigation_service.models import CollectedContextResponse, Finding, NormalizedInvestigationRequest, TargetRef
from investigation_service.synthesis import build_primary_evidence, build_root_cause_report


def test_workload_findings_do_not_include_otel_pipeline_noise() -> None:
    findings = derive_findings(
        "workload",
        {"kind": "pod", "name": "crashy"},
        ["BackOff restarting failed container"],
        "starting\nexit 1",
        {"accepted_spans_per_sec": 0, "pod_restart_rate": 0.0034},
    )

    titles = [item.title for item in findings]
    assert "Crash Loop Detected" in titles
    assert "Pod Restarts Increasing" in titles
    assert "No Active Span Ingestion" not in titles


def test_explicit_pod_request_uses_pod_specific_likely_cause() -> None:
    request = NormalizedInvestigationRequest(
        source="manual",
        scope="workload",
        namespace="kagent-smoke",
        target="pod/crashy",
        profile="workload",
        lookback_minutes=15,
        normalization_notes=[],
    )
    report = build_root_cause_report(
        CollectedContextResponse(
            target=TargetRef(namespace="kagent-smoke", kind="pod", name="crashy-abc123"),
            object_state={"kind": "pod", "name": "crashy-abc123"},
            events=["BackOff restarting failed container"],
            log_excerpt="starting",
            metrics={"pod_restart_rate": 0.0034},
            findings=[
                Finding(
                    severity="critical",
                    source="events",
                    title="Crash Loop Detected",
                    evidence="Events indicate BackOff/CrashLoopBackOff behavior",
                )
            ],
            limitations=[],
            enrichment_hints=[],
        ),
        request,
    )

    assert report.likely_cause == "The pod is repeatedly failing shortly after start, so Kubernetes is backing off restarts."


def test_operator_ownership_hint_flows_into_follow_ups() -> None:
    request = NormalizedInvestigationRequest(
        source="manual",
        scope="workload",
        namespace="operator-smoke",
        target="pod/crashy",
        profile="workload",
        lookback_minutes=15,
        normalization_notes=[],
    )
    report = build_root_cause_report(
        CollectedContextResponse(
            target=TargetRef(namespace="operator-smoke", kind="pod", name="crashy-abc123"),
            object_state={
                "kind": "pod",
                "name": "crashy-abc123",
                "labels": {
                    "app.kubernetes.io/managed-by": "homelab-operator",
                    "homelab.erauner.dev/owner-kind": "Backend",
                    "homelab.erauner.dev/owner-name": "crashy",
                },
            },
            events=["BackOff restarting failed container"],
            log_excerpt="starting",
            metrics={"pod_restart_rate": 0.0034},
            findings=[
                Finding(
                    severity="critical",
                    source="events",
                    title="Crash Loop Detected",
                    evidence="Events indicate BackOff/CrashLoopBackOff behavior",
                )
            ],
            limitations=[],
            enrichment_hints=[
                "operator-managed workload (homelab-operator); owner appears to be Backend/crashy. Prefer checking operator reconciliation and updating the owning resource rather than editing pods directly."
            ],
        ),
        request,
    )

    assert any("Backend/crashy" in item for item in report.suggested_follow_ups)


def test_backend_normalization_note_flows_into_follow_ups() -> None:
    request = NormalizedInvestigationRequest(
        source="manual",
        scope="workload",
        namespace="operator-smoke",
        target="deployment/crashy",
        service_name="crashy",
        profile="workload",
        lookback_minutes=15,
        normalization_notes=["resolved Backend/crashy to deployment/crashy"],
    )
    report = build_root_cause_report(
        CollectedContextResponse(
            target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
            object_state={"kind": "deployment", "name": "crashy"},
            events=["BackOff restarting failed container"],
            log_excerpt="starting",
            metrics={"pod_restart_rate": 0.0034},
            findings=[
                Finding(
                    severity="critical",
                    source="events",
                    title="Crash Loop Detected",
                    evidence="Events indicate BackOff/CrashLoopBackOff behavior",
                )
            ],
            limitations=[],
            enrichment_hints=[],
        ),
        request,
    )

    assert any("Backend/crashy" in item for item in report.suggested_follow_ups)
    assert any("resolved to deployment/crashy" in item for item in report.suggested_follow_ups)


def test_workload_findings_include_service_enrichment_when_present() -> None:
    findings = derive_findings(
        "workload",
        {"kind": "deployment", "name": "metrics-api"},
        ["deployment available"],
        "healthy",
        {
            "pod_restart_rate": 0.0,
            "service_error_rate": 0.2,
            "service_latency_p95_seconds": 1.8,
        },
    )

    titles = {item.title for item in findings}
    assert "Service Returning 5xx Responses" in titles
    assert "High Service Latency" in titles


def test_workload_findings_prefer_init_container_blockage_over_podinitializing_noise() -> None:
    findings = derive_findings(
        "workload",
        {
            "kind": "pod",
            "name": "toolbridge-api-migrate-j5wwf",
            "initContainers": [
                {
                    "name": "wait-for-postgres",
                    "waitingReason": "PodInitializing",
                    "lastTerminationReason": "Error",
                    "lastTerminationExitCode": 1,
                    "restartCount": 1,
                    "command": ["/bin/sh"],
                    "args": ["/app/scripts/wait-for-postgres.sh"],
                }
            ],
            "containers": [
                {
                    "name": "migrate",
                    "waitingReason": "PodInitializing",
                    "command": ["/bin/sh"],
                    "args": ["/app/scripts/migrate.sh"],
                }
            ],
        },
        [],
        "Waiting for postgres...\nconnection refused",
        {},
    )

    titles = [item.title for item in findings]
    assert titles[0] == "Init Container Dependency Blocked"
    assert "Container Restart Failure Details" not in titles


def test_explicit_pod_request_uses_init_block_likely_cause_when_init_container_is_stuck() -> None:
    request = NormalizedInvestigationRequest(
        source="manual",
        scope="workload",
        namespace="toolbridge",
        target="pod/toolbridge-api-migrate-j5wwf",
        profile="workload",
        lookback_minutes=15,
        normalization_notes=[],
    )
    report = build_root_cause_report(
        CollectedContextResponse(
            target=TargetRef(namespace="toolbridge", kind="pod", name="toolbridge-api-migrate-j5wwf"),
            object_state={
                "kind": "pod",
                "name": "toolbridge-api-migrate-j5wwf",
                "initContainers": [
                    {
                        "name": "wait-for-postgres",
                        "waitingReason": "PodInitializing",
                        "lastTerminationReason": "Error",
                        "lastTerminationExitCode": 1,
                        "restartCount": 1,
                    }
                ],
                "containers": [{"name": "migrate", "waitingReason": "PodInitializing"}],
            },
            events=[],
            log_excerpt="Waiting for postgres...\nconnection refused",
            metrics={},
            findings=[
                Finding(
                    severity="critical",
                    source="k8s",
                    title="Init Container Dependency Blocked",
                    evidence=(
                        "init container=wait-for-postgres, waiting reason=PodInitializing, "
                        "termination reason=Error, exit code=1, restarts=1, "
                        "init logs indicate dependency wait or connection failure"
                    ),
                )
            ],
            limitations=[],
            enrichment_hints=[],
        ),
        request,
    )

    assert report.diagnosis == "Init Container Dependency Blocked"
    assert report.likely_cause == (
        "Init container 'wait-for-postgres' is blocked in PodInitializing, preventing the workload from completing startup."
    )


def test_workload_findings_use_runtime_pod_init_status_for_deployments() -> None:
    findings = derive_findings(
        "workload",
        {
            "kind": "deployment",
            "name": "toolbridge-api-migrate",
            "runtimePod": {
                "kind": "pod",
                "name": "toolbridge-api-migrate-j5wwf",
                "initContainers": [
                    {
                        "name": "wait-for-postgres",
                        "waitingReason": "PodInitializing",
                        "lastTerminationReason": "Error",
                        "lastTerminationExitCode": 1,
                        "restartCount": 1,
                    }
                ],
                "containers": [{"name": "migrate", "waitingReason": "PodInitializing"}],
            },
        },
        [],
        "Waiting for postgres...\nconnection refused",
        {},
    )

    assert findings[0].title == "Init Container Dependency Blocked"


def test_workload_service_degradation_outranks_generic_log_patterns() -> None:
    request = NormalizedInvestigationRequest(
        source="manual",
        scope="workload",
        namespace="operator-metrics-smoke",
        target="deployment/api",
        service_name="api",
        profile="workload",
        lookback_minutes=15,
        normalization_notes=[],
    )
    findings = derive_findings(
        "workload",
        {"kind": "deployment", "name": "api"},
        ["Normal ScalingReplicaSet scaled up replica set api from 0 to 1"],
        "handled request error: upstream returned 500",
        {
            "service_request_rate": 0.75,
            "service_error_rate": 0.06,
            "service_latency_p95_seconds": 1.63,
        },
    )
    report = build_root_cause_report(
        CollectedContextResponse(
            target=TargetRef(namespace="operator-metrics-smoke", kind="deployment", name="api"),
            object_state={"kind": "deployment", "name": "api"},
            events=["Normal ScalingReplicaSet scaled up replica set api from 0 to 1"],
            log_excerpt="handled request error: upstream returned 500",
            metrics={
                "service_request_rate": 0.75,
                "service_error_rate": 0.06,
                "service_latency_p95_seconds": 1.63,
            },
            findings=findings,
            limitations=[],
            enrichment_hints=[],
        ),
        request,
    )

    assert report.diagnosis == "Service Returning 5xx Responses"
    assert any("request rate over lookback window" in item for item in report.evidence)


def test_build_primary_evidence_adds_service_request_rate_metric_item() -> None:
    findings = derive_findings(
        "workload",
        {"kind": "deployment", "name": "api"},
        [],
        "",
        {
            "service_request_rate": 0.75,
            "service_error_rate": 0.06,
            "service_latency_p95_seconds": 1.63,
        },
    )
    evidence_items = build_primary_evidence(
        CollectedContextResponse(
            target=TargetRef(namespace="operator-metrics-smoke", kind="deployment", name="api"),
            object_state={"kind": "deployment", "name": "api"},
            events=[],
            log_excerpt="",
            metrics={
                "service_request_rate": 0.75,
                "service_error_rate": 0.06,
                "service_latency_p95_seconds": 1.63,
            },
            findings=findings,
            limitations=[],
            enrichment_hints=[],
        ),
        "workload",
    )

    assert any(item.summary == "prometheus: Service Request Rate" for item in evidence_items)
