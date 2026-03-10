from investigation_orchestrator import OrchestratorRuntimeConfig
from investigation_service.models import CorrelatedChange, EvidenceItem, InvestigationReport
from investigation_shadow_runtime.host_adapter import format_shadow_report, parse_shadow_task
from investigation_shadow_runtime.runner import run_shadow_investigation


def test_parse_shadow_task_supports_vague_workload_prompt(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_shadow_runtime.host_adapter.find_unhealthy_pod",
        lambda req: type("Response", (), {"candidate": type("Candidate", (), {"target": "pod/crashy"})()})(),
    )

    request = parse_shadow_task(
        "Investigate the unhealthy pod in namespace kagent-smoke. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step."
    )

    assert request.namespace == "kagent-smoke"
    assert request.target == "pod/crashy"
    assert request.profile == "workload"


def test_parse_shadow_task_supports_alert_blocks() -> None:
    request = parse_shadow_task(
        """
        Alert: PodCrashLooping
        Namespace: kagent-smoke
        Pod: crashy
        """
    )

    assert request.alertname == "PodCrashLooping"
    assert request.namespace == "kagent-smoke"
    assert request.target == "pod/crashy"
    assert request.profile == "workload"


def test_format_shadow_report_uses_fixed_sections() -> None:
    report = InvestigationReport(
        cluster="erauner-home",
        scope="workload",
        target="deployment/crashy",
        diagnosis="CrashLoopBackOff",
        confidence="high",
        evidence=["Container exits immediately."],
        related_data=[
            CorrelatedChange(
                fingerprint="event|1",
                timestamp="2026-03-10T12:00:00Z",
                source="k8s_event",
                resource_kind="Deployment",
                namespace="kagent-smoke",
                name="crashy",
                relation="same_workload",
                summary="Deployment rolled out shortly before failures.",
                confidence="medium",
            )
        ],
        limitations=["No trace data was available."],
        recommended_next_step="Inspect the failing container command.",
    )

    rendered = format_shadow_report(report)

    assert "## Diagnosis" in rendered
    assert "## Evidence" in rendered
    assert "## Related Data" in rendered
    assert "## Limitations" in rendered
    assert "## Recommended next step" in rendered


def test_format_shadow_report_prefers_structured_evidence_items() -> None:
    report = InvestigationReport(
        cluster="erauner-home",
        scope="workload",
        target="pod/crashy-abc123",
        diagnosis="Crash Loop Detected",
        confidence="high",
        evidence=[
            "events: Crash Loop Detected - Events indicate BackOff/CrashLoopBackOff behavior",
            "recent events - # The following events (YAML format) were found: InvolvedObject: Kind: Pod Name: crashy-abc123 Timestamp: ...",
        ],
        evidence_items=[
            EvidenceItem(
                fingerprint="finding|1",
                source="k8s",
                kind="finding",
                severity="critical",
                summary="k8s: Crash Loop Detected",
                detail="container=crashy, waiting reason=CrashLoopBackOff, restarts=5",
            ),
            EvidenceItem(
                fingerprint="event|1",
                source="events",
                kind="event",
                severity="warning",
                summary="recent events",
                detail="Warning BackOff Back-off restarting failed container crashy in pod crashy-abc123",
            ),
        ],
        related_data=[],
        limitations=[],
        recommended_next_step="Inspect recent logs before taking write actions.",
    )

    rendered = format_shadow_report(report)

    assert "recent events: Back-off restarting failed container crashy in pod crashy-abc123" in rendered
    assert "Crash Loop Detected - container=crashy, waiting reason=CrashLoopBackOff, restarts=5" not in rendered
    assert "InvolvedObject:" not in rendered


def test_run_shadow_investigation_formats_orchestrator_report(monkeypatch) -> None:
    def fake_run(_req, *, runtime=None):
        assert isinstance(runtime, OrchestratorRuntimeConfig)
        return type(
            "Result",
            (),
            {
                "status": "completed",
                "final_report": InvestigationReport(
                    cluster="erauner-home",
                    scope="workload",
                    target="deployment/crashy",
                    diagnosis="CrashLoopBackOff",
                    confidence="high",
                    evidence=["Container exits immediately."],
                    related_data=[],
                    related_data_note="No meaningful correlated changes found in the requested time window.",
                    limitations=[],
                    recommended_next_step="Inspect the failing container command.",
                ),
                "next_nodes": (),
            },
        )()

    monkeypatch.setattr("investigation_shadow_runtime.runner.run_orchestrated_investigation_runtime", fake_run)

    result = run_shadow_investigation(
        "Investigate deployment/crashy in namespace kagent-smoke.",
        runtime=OrchestratorRuntimeConfig(),
    )

    assert result.runtime_status == "completed"
    assert "## Diagnosis" in result.markdown
    assert "CrashLoopBackOff" in result.markdown
