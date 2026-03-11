import asyncio
from types import SimpleNamespace

import httpx
import pytest

from investigation_orchestrator import OrchestratorRuntimeConfig
from investigation_service.models import CorrelatedChange, EvidenceItem, InvestigationReport
from investigation_service.presentation import render_presentation_markdown
from investigation_shadow_runtime.a2a_app import build_shadow_app
from investigation_shadow_runtime.checkpoint_adapter import ShadowKAgentCheckpointer
from investigation_shadow_runtime.host_adapter import format_shadow_report, parse_shadow_task
from investigation_shadow_runtime.runner import run_shadow_investigation


def _resolved_target(*, req, target: str, profile: str = "workload", node_name: str | None = None):
    return type(
        "ResolvedTarget",
        (),
        {
            "cluster": req.cluster,
            "namespace": req.namespace,
            "target": target,
            "profile": profile,
            "service_name": None,
            "node_name": node_name,
        },
    )()


def test_parse_shadow_task_supports_vague_workload_prompt(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_shadow_runtime.host_adapter.resolve_primary_target",
        lambda req: _resolved_target(req=req, target="pod/crashy"),
    )

    request = parse_shadow_task(
        "Investigate the unhealthy pod in namespace kagent-smoke. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step."
    )

    assert request.namespace == "kagent-smoke"
    assert request.target == "pod/crashy"
    assert request.profile == "workload"


def test_parse_shadow_task_supports_alert_blocks() -> None:
    captured = {}

    def _resolve(req):
        captured["request"] = req
        return _resolved_target(req=req, target="pod/crashy")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("investigation_shadow_runtime.host_adapter.resolve_primary_target", _resolve)
    try:
        request = parse_shadow_task(
            """
            Alert: PodCrashLooping
            Namespace: kagent-smoke
            Pod: crashy
            """
        )
    finally:
        monkeypatch.undo()

    assert captured["request"].question is not None
    assert "PodCrashLooping" in captured["request"].question
    assert request.alertname == "PodCrashLooping"
    assert request.namespace == "kagent-smoke"
    assert request.target == "pod/crashy"
    assert request.profile == "workload"


def test_parse_shadow_task_supports_direct_node_target(monkeypatch) -> None:
    monkeypatch.setattr(
        "investigation_shadow_runtime.host_adapter.resolve_primary_target",
        lambda req: _resolved_target(req=req, target="node/worker3", node_name="worker3"),
    )

    request = parse_shadow_task(
        "Investigate node/worker3 for memory pressure."
    )

    assert request.target == "node/worker3"
    assert request.node_name == "worker3"
    assert request.profile == "workload"


def test_parse_shadow_task_does_not_accept_unsupported_job_or_daemonset_targets(monkeypatch) -> None:
    captured = {}

    def _resolve(req):
        captured["request"] = req
        return _resolved_target(req=req, target="pod/fallback")

    monkeypatch.setattr("investigation_shadow_runtime.host_adapter.resolve_primary_target", _resolve)

    parse_shadow_task("Investigate job/backup-runner and daemonset/node-agent in namespace ops.")

    assert captured["request"].target is None


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
    assert rendered == render_presentation_markdown(report, profile="operator_summary")


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


def test_run_shadow_investigation_applies_pod_compatibility(monkeypatch) -> None:
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
                    target="pod/crashy",
                    diagnosis="CrashLoopBackOff",
                    confidence="high",
                    evidence=["Container exits immediately."],
                    related_data=[],
                    limitations=[],
                    recommended_next_step="Inspect the failing container command.",
                ),
                "next_nodes": (),
            },
        )()

    monkeypatch.setattr("investigation_shadow_runtime.runner.run_orchestrated_investigation_runtime", fake_run)
    monkeypatch.setattr(
        "investigation_shadow_runtime.runner._maybe_attach_resolved_pod_context",
        lambda req, report: report.model_copy(
            update={"evidence": [*report.evidence, "Resolved concrete crash-looping pod: pod/crashy-abc123"]}
        ),
    )

    result = run_shadow_investigation(
        "Alert: PodCrashLooping\nNamespace: kagent-smoke\nPod: crashy",
        runtime=OrchestratorRuntimeConfig(),
    )

    assert "Resolved concrete crash-looping pod: pod/crashy-abc123" in result.markdown


def test_run_shadow_investigation_fails_closed_on_interrupted_review(monkeypatch) -> None:
    def fake_run(_req, *, runtime=None):
        assert isinstance(runtime, OrchestratorRuntimeConfig)
        return type(
            "Result",
            (),
            {
                "status": "interrupted",
                "final_report": None,
                "next_nodes": ("apply_exploration_review",),
            },
        )()

    monkeypatch.setattr("investigation_shadow_runtime.runner.run_orchestrated_investigation_runtime", fake_run)

    with pytest.raises(ValueError, match="shadow investigation interrupted before completion"):
        run_shadow_investigation(
            "Investigate deployment/crashy in namespace kagent-smoke.",
            runtime=OrchestratorRuntimeConfig(),
        )


def test_build_shadow_app_disables_thread_dump_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SHADOW_DEBUG_ENDPOINTS_ENABLED", raising=False)
    config = SimpleNamespace(app_name="incident-triage-shadow")
    app = build_shadow_app(
        graph=None,  # type: ignore[arg-type]
        agent_card={
            "name": "incident-triage-shadow",
            "description": "test",
            "url": "http://example.com",
            "version": "0.1.0",
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "capabilities": {},
            "skills": [],
        },
        config=config,  # type: ignore[arg-type]
        tracing=False,
    )

    paths = {route.path for route in app.routes}
    assert "/health" in paths
    assert "/thread_dump" not in paths


def test_build_shadow_app_enables_thread_dump_when_requested(monkeypatch) -> None:
    monkeypatch.setenv("SHADOW_DEBUG_ENDPOINTS_ENABLED", "true")
    config = SimpleNamespace(app_name="incident-triage-shadow")
    app = build_shadow_app(
        graph=None,  # type: ignore[arg-type]
        agent_card={
            "name": "incident-triage-shadow",
            "description": "test",
            "url": "http://example.com",
            "version": "0.1.0",
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "capabilities": {},
            "skills": [],
        },
        config=config,  # type: ignore[arg-type]
        tracing=False,
    )

    paths = {route.path for route in app.routes}
    assert "/thread_dump" in paths


def test_build_shadow_app_keeps_thread_dump_disabled_for_falsey_value(monkeypatch) -> None:
    monkeypatch.setenv("SHADOW_DEBUG_ENDPOINTS_ENABLED", "false")
    config = SimpleNamespace(app_name="incident-triage-shadow")
    app = build_shadow_app(
        graph=None,  # type: ignore[arg-type]
        agent_card={
            "name": "incident-triage-shadow",
            "description": "test",
            "url": "http://example.com",
            "version": "0.1.0",
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "capabilities": {},
            "skills": [],
        },
        config=config,  # type: ignore[arg-type]
        tracing=False,
    )

    paths = {route.path for route in app.routes}
    assert "/thread_dump" not in paths


def test_shadow_checkpointer_alist_preserves_extra_config_fields() -> None:
    async def run() -> None:
        probe = ShadowKAgentCheckpointer(
            client=httpx.AsyncClient(base_url="http://example.com"),
            app_name="incident-triage-shadow",
        )
        checkpoint_type, checkpoint_bytes = probe.serde.dumps_typed({"id": "cp-1", "v": 1})
        metadata_bytes = b'{"source":"shadow"}'

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["X-User-ID"] == "shadow-user@example.com"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "thread_id": "thread-2",
                            "checkpoint_ns": "shadow-listed",
                            "checkpoint_id": "cp-1",
                            "parent_checkpoint_id": None,
                            "checkpoint": __import__("base64").b64encode(checkpoint_bytes).decode("ascii"),
                            "metadata": __import__("base64").b64encode(metadata_bytes).decode("ascii"),
                            "type_": checkpoint_type,
                            "writes": None,
                        }
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://example.com") as client:
            checkpointer = ShadowKAgentCheckpointer(client=client, app_name="incident-triage-shadow")
            config = {
                "configurable": {
                    "thread_id": "thread-1",
                    "checkpoint_ns": "shadow-seed",
                    "checkpoint_id": "seed",
                    "user_id": "shadow-user@example.com",
                }
            }
            tuples = [item async for item in checkpointer.alist(config, limit=1)]

        assert len(tuples) == 1
        assert tuples[0].config["configurable"]["thread_id"] == "thread-2"
        assert tuples[0].config["configurable"]["checkpoint_ns"] == "shadow-listed"
        assert tuples[0].config["configurable"]["checkpoint_id"] == "cp-1"
        assert tuples[0].config["configurable"]["user_id"] == "shadow-user@example.com"
        await probe.client.aclose()

    asyncio.run(run())
