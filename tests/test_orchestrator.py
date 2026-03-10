from investigation_orchestrator.entrypoint import run_orchestrated_investigation
from investigation_orchestrator.checkpointing import GraphCheckpointConfig, create_in_memory_checkpointer
from investigation_orchestrator.graph import get_investigation_graph_state
from investigation_service.models import (
    ActiveEvidenceBatchContract,
    AdvanceInvestigationRuntimeResponse,
    BuildInvestigationPlanRequest,
    EvidenceBatch,
    EvidenceBatchExecution,
    EvidenceStepContract,
    InvestigationReport,
    InvestigationReportRequest,
    InvestigationSubject,
    InvestigationTarget,
    PlanStep,
    ReportingExecutionContext,
    StepExecutionInputs,
    SubmittedStepArtifact,
)
import investigation_orchestrator.entrypoint as entrypoint
import pytest


def _incident() -> BuildInvestigationPlanRequest:
    return BuildInvestigationPlanRequest(
        cluster="erauner-home",
        namespace="operator-smoke",
        target="pod/crashy",
        profile="workload",
        lookback_minutes=15,
        alertname="PodCrashLooping",
        labels={"pod": "crashy"},
        annotations={"summary": "Pod crashlooping"},
    )


def _context(
    active_batch_id: str | None = "batch-1",
    *,
    batch_ids: list[str] | None = None,
    render_only_active: bool = False,
) -> ReportingExecutionContext:
    if batch_ids is None:
        batch_ids = [item for item in [active_batch_id] if item is not None]
    steps: list[PlanStep] = []
    evidence_batches: list[EvidenceBatch] = []
    for batch_id in batch_ids:
        if render_only_active and batch_id == active_batch_id:
            step_id = f"{batch_id}-render"
            steps.append(
                PlanStep(
                    id=step_id,
                    title="Render report",
                    category="render",
                    plane="report",
                    rationale="Render the final report",
                    suggested_capability="render_investigation_report",
                )
            )
            evidence_batches.append(
                EvidenceBatch(
                    id=batch_id,
                    title="Render",
                    status="pending",
                    intent="Render the final report.",
                    step_ids=[step_id],
                )
            )
        else:
            step_id = f"{batch_id}-evidence"
            steps.append(
                PlanStep(
                    id=step_id,
                    title="Collect evidence",
                    category="evidence",
                    plane="workload",
                    rationale="Collect evidence",
                    suggested_capability="workload_evidence_plane",
                )
            )
            evidence_batches.append(
                EvidenceBatch(
                    id=batch_id,
                    title="Evidence",
                    status="pending",
                    intent="Collect evidence.",
                    step_ids=[step_id],
                )
            )

    return ReportingExecutionContext(
        updated_plan={
            "mode": "alert_rca",
            "objective": "Investigate PodCrashLooping",
            "target": {
                "source": "alert",
                "scope": "workload",
                "cluster": "erauner-home",
                "namespace": "operator-smoke",
                "requested_target": "pod/crashy",
                "target": "pod/crashy-abc123",
                "profile": "workload",
                "service_name": None,
                "node_name": None,
                "lookback_minutes": 15,
                "normalization_notes": ["alertname=PodCrashLooping"],
            },
            "steps": steps,
            "evidence_batches": evidence_batches,
            "active_batch_id": active_batch_id,
            "planning_notes": [],
        },
        executions=[],
        initial_plan=None,
        allow_bounded_fallback_execution=False,
    )


def test_run_orchestrated_investigation_advances_and_renders(monkeypatch) -> None:
    incident = _incident()
    captured: dict[str, list[SubmittedStepArtifact]] = {"submitted": []}
    monkeypatch.setattr(
        entrypoint,
        "find_unhealthy_pod",
        lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})(),
    )

    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context())
    monkeypatch.setattr(
        entrypoint,
        "get_active_batch",
        lambda *_args, **_kwargs: ActiveEvidenceBatchContract(
            batch_id="batch-1",
            title="Initial evidence",
            intent="Collect alert and workload evidence",
            subject=InvestigationSubject(
                source="alert",
                kind="alert",
                summary="Investigate PodCrashLooping",
                requested_target="pod/crashy",
                alertname="PodCrashLooping",
            ),
            canonical_target=InvestigationTarget(
                source="alert",
                scope="workload",
                cluster="erauner-home",
                namespace="operator-smoke",
                requested_target="pod/crashy",
                target="pod/crashy-abc123",
                service_name=None,
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=["alertname=PodCrashLooping"],
            ),
            steps=[
                EvidenceStepContract(
                    step_id="collect-target-evidence",
                    title="Collect workload evidence",
                    plane="workload",
                    artifact_type="evidence_bundle",
                    requested_capability="workload_evidence_plane",
                    preferred_mcp_server="kubernetes-mcp-server",
                    preferred_tool_names=["pods_log", "resources_get"],
                    fallback_mcp_server=None,
                    fallback_tool_names=[],
                    execution_mode="external_preferred",
                    execution_inputs=StepExecutionInputs(
                        request_kind="target_context",
                        cluster="erauner-home",
                        namespace="operator-smoke",
                        target="pod/crashy-abc123",
                        profile="workload",
                        lookback_minutes=15,
                        alertname="PodCrashLooping",
                        labels={"pod": "crashy"},
                        annotations={"summary": "Pod crashlooping"},
                    ),
                )
            ],
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "run_required_external_steps",
        lambda _batch: [
            SubmittedStepArtifact(
                step_id="collect-target-evidence",
                actual_route={
                    "source_kind": "investigation_internal",
                    "mcp_server": "investigation-mcp-server",
                    "tool_name": "collect_workload_evidence",
                    "tool_path": ["investigation_orchestrator.evidence_runner", "collect_workload_evidence"],
                },
                evidence_bundle={
                    "cluster": "erauner-home",
                    "target": {"namespace": "operator-smoke", "kind": "pod", "name": "crashy-abc123"},
                    "object_state": {"kind": "pod", "name": "crashy-abc123"},
                    "events": ["Warning BackOff"],
                    "log_excerpt": "panic: startup failed",
                    "metrics": {},
                    "findings": [
                        {
                            "severity": "critical",
                            "source": "logs",
                            "title": "Crash Loop Detected",
                            "evidence": "Container restarts are increasing.",
                        }
                    ],
                    "limitations": [],
                    "enrichment_hints": [],
                },
            )
        ],
    )

    def fake_advance(_incident, _execution_context, *, submitted_steps, batch_id=None):
        captured["submitted"] = submitted_steps
        return AdvanceInvestigationRuntimeResponse(
            execution_context=_context(active_batch_id=None),
            next_active_batch=None,
        )

    monkeypatch.setattr(entrypoint, "advance_batch", fake_advance)
    monkeypatch.setattr(
        entrypoint,
        "render_report",
        lambda *_args, **_kwargs: InvestigationReport(
            cluster="erauner-home",
            scope="workload",
            target="pod/crashy-abc123",
            diagnosis="Crash Loop Detected",
            confidence="high",
            evidence=["panic: startup failed"],
            evidence_items=[],
            related_data=[],
            related_data_note="No meaningful correlated changes found in the requested time window.",
            limitations=["No rollout data was available."],
            recommended_next_step="Inspect logs and recent deployment changes before taking write actions.",
            suggested_follow_ups=[],
            guidelines=[],
            normalization_notes=["alertname=PodCrashLooping"],
            tool_path_trace=None,
        ),
    )

    report = run_orchestrated_investigation(
        InvestigationReportRequest(
            cluster=incident.cluster,
            namespace=incident.namespace,
            target=incident.target,
            profile=incident.profile,
            lookback_minutes=incident.lookback_minutes,
            alertname=incident.alertname,
            labels=incident.labels,
            annotations=incident.annotations,
        )
    )

    assert captured["submitted"]
    assert captured["submitted"][0].step_id == "collect-target-evidence"
    assert report.target == "pod/crashy-abc123"
    assert report.diagnosis == "Crash Loop Detected"


def test_run_orchestrated_investigation_forwards_workload_peer_failure_metadata(monkeypatch) -> None:
    incident = _incident()
    captured = {"submitted": None}

    monkeypatch.setattr(
        entrypoint,
        "find_unhealthy_pod",
        lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})(),
    )
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context())
    monkeypatch.setattr(
        entrypoint,
        "get_active_batch",
        lambda *_args, **_kwargs: ActiveEvidenceBatchContract(
            batch_id="batch-1",
            title="Initial evidence",
            intent="Collect workload evidence",
            subject=InvestigationSubject(
                source="alert",
                kind="alert",
                summary="Investigate PodCrashLooping",
                requested_target="pod/crashy",
                alertname="PodCrashLooping",
            ),
            canonical_target=InvestigationTarget(
                source="alert",
                scope="workload",
                cluster="erauner-home",
                namespace="operator-smoke",
                requested_target="pod/crashy",
                target="pod/crashy-abc123",
                service_name=None,
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=["alertname=PodCrashLooping"],
            ),
            steps=[
                EvidenceStepContract(
                    step_id="collect-target-evidence",
                    title="Collect workload evidence",
                    plane="workload",
                    artifact_type="evidence_bundle",
                    requested_capability="workload_evidence_plane",
                    preferred_mcp_server="kubernetes-mcp-server",
                    preferred_tool_names=["pods_log", "resources_get"],
                    fallback_mcp_server=None,
                    fallback_tool_names=[],
                    execution_mode="external_preferred",
                    execution_inputs=StepExecutionInputs(
                        request_kind="target_context",
                        cluster="erauner-home",
                        namespace="operator-smoke",
                        target="pod/crashy-abc123",
                        profile="workload",
                        lookback_minutes=15,
                    ),
                )
            ],
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "run_required_external_steps",
        lambda _batch: [
            SubmittedStepArtifact(
                step_id="collect-target-evidence",
                actual_route={
                    "source_kind": "peer_mcp",
                    "mcp_server": "kubernetes-mcp-server",
                    "tool_name": "resources_get",
                    "tool_path": ["kubernetes-mcp-server", "resources_get", "events_list", "pods_log"],
                },
                limitations=["peer workload MCP attempt failed: peer unavailable"],
            )
        ],
    )

    def fake_advance(_incident, _execution_context, *, submitted_steps, batch_id=None):
        captured["submitted"] = submitted_steps
        return AdvanceInvestigationRuntimeResponse(
            execution_context=_context(active_batch_id=None),
            next_active_batch=None,
        )

    monkeypatch.setattr(entrypoint, "advance_batch", fake_advance)
    monkeypatch.setattr(
        entrypoint,
        "render_report",
        lambda *_args, **_kwargs: InvestigationReport(
            cluster="erauner-home",
            scope="workload",
            target="pod/crashy-abc123",
            diagnosis="Crash Loop Detected",
            confidence="high",
            evidence=["panic: startup failed"],
            evidence_items=[],
            related_data=[],
            related_data_note="No meaningful correlated changes found in the requested time window.",
            limitations=[],
            recommended_next_step="Inspect logs and recent deployment changes before taking write actions.",
            suggested_follow_ups=[],
            guidelines=[],
            normalization_notes=["alertname=PodCrashLooping"],
            tool_path_trace=None,
        ),
    )

    report = run_orchestrated_investigation(
        InvestigationReportRequest(
            cluster=incident.cluster,
            namespace=incident.namespace,
            target=incident.target,
            profile=incident.profile,
            lookback_minutes=incident.lookback_minutes,
            alertname=incident.alertname,
            labels=incident.labels,
            annotations=incident.annotations,
        )
    )

    assert captured["submitted"] is not None
    assert captured["submitted"][0].actual_route.mcp_server == "kubernetes-mcp-server"
    assert "peer workload MCP attempt failed: peer unavailable" in captured["submitted"][0].limitations
    assert report.target == "pod/crashy-abc123"


def test_run_orchestrated_investigation_bypasses_terminal_render_batch(monkeypatch) -> None:
    incident = _incident()
    monkeypatch.setattr(
        entrypoint,
        "find_unhealthy_pod",
        lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})(),
    )

    monkeypatch.setattr(
        entrypoint,
        "seed_context",
        lambda *_args, **_kwargs: _context(active_batch_id="batch-2", batch_ids=["batch-2"], render_only_active=True),
    )
    monkeypatch.setattr(
        entrypoint,
        "get_active_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("get_active_batch should not be called for render-only batch")),
    )
    monkeypatch.setattr(
        entrypoint,
        "render_report",
        lambda *_args, **_kwargs: InvestigationReport(
            cluster="erauner-home",
            scope="workload",
            target="pod/crashy-abc123",
            diagnosis="Crash Loop Detected",
            confidence="high",
            evidence=["panic: startup failed"],
            evidence_items=[],
            related_data=[],
            related_data_note="No meaningful correlated changes found in the requested time window.",
            limitations=["No rollout data was available."],
            recommended_next_step="Inspect logs and recent deployment changes before taking write actions.",
            suggested_follow_ups=[],
            guidelines=[],
            normalization_notes=["alertname=PodCrashLooping"],
            tool_path_trace=None,
        ),
    )

    report = run_orchestrated_investigation(
        InvestigationReportRequest(
            cluster=incident.cluster,
            namespace=incident.namespace,
            target=incident.target,
            profile=incident.profile,
            lookback_minutes=incident.lookback_minutes,
            alertname=incident.alertname,
            labels=incident.labels,
            annotations=incident.annotations,
        )
    )

    assert report.target == "pod/crashy-abc123"


def test_run_orchestrated_investigation_renders_immediately_when_no_active_batch(monkeypatch) -> None:
    incident = _incident()
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context(active_batch_id=None))
    monkeypatch.setattr(
        entrypoint,
        "get_active_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("get_active_batch should not run when no active batch remains")),
    )
    monkeypatch.setattr(
        entrypoint,
        "render_report",
        lambda *_args, **_kwargs: InvestigationReport(
            cluster="erauner-home",
            scope="workload",
            target="pod/crashy-abc123",
            diagnosis="Crash Loop Detected",
            confidence="high",
            evidence=["panic: startup failed"],
            evidence_items=[],
            related_data=[],
            related_data_note="No meaningful correlated changes found in the requested time window.",
            limitations=[],
            recommended_next_step="Inspect logs and recent deployment changes before taking write actions.",
            suggested_follow_ups=[],
            guidelines=[],
            normalization_notes=["alertname=PodCrashLooping"],
            tool_path_trace=None,
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "find_unhealthy_pod",
        lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})(),
    )

    report = run_orchestrated_investigation(
        InvestigationReportRequest(
            cluster=incident.cluster,
            namespace=incident.namespace,
            target=incident.target,
            profile=incident.profile,
            lookback_minutes=incident.lookback_minutes,
            alertname=incident.alertname,
            labels=incident.labels,
            annotations=incident.annotations,
        )
    )

    assert report.target == "pod/crashy-abc123"


def test_run_orchestrated_investigation_fails_when_budget_exhausted_with_non_render_work(monkeypatch) -> None:
    incident = _incident()
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context(active_batch_id="batch-1"))

    try:
        run_orchestrated_investigation(
            InvestigationReportRequest(
                cluster=incident.cluster,
                namespace=incident.namespace,
                target=incident.target,
                profile=incident.profile,
                lookback_minutes=incident.lookback_minutes,
                alertname=incident.alertname,
                labels=incident.labels,
                annotations=incident.annotations,
            ),
            max_batches=0,
        )
    except ValueError as exc:
        assert "non-render work still pending" in str(exc)
    else:
        raise AssertionError("expected orchestrator to fail when budget is exhausted before non-render work is complete")


def test_run_orchestrated_investigation_attaches_resolved_concrete_pod_for_alerts(monkeypatch) -> None:
    incident = _incident()
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context(active_batch_id=None))
    monkeypatch.setattr(
        entrypoint,
        "render_report",
        lambda *_args, **_kwargs: InvestigationReport(
            cluster="erauner-home",
            scope="workload",
            target="deployment/crashy",
            diagnosis="Crash Loop Detected",
            confidence="high",
            evidence=[
                "Alert PodCrashLooping requested pod/crashy",
                "Resolved runtime target: deployment/crashy",
            ],
            evidence_items=[],
            related_data=[],
            related_data_note="No meaningful correlated changes found in the requested time window.",
            limitations=[],
            recommended_next_step="Inspect logs and recent deployment changes before taking write actions.",
            suggested_follow_ups=[],
            guidelines=[],
            normalization_notes=["alertname=PodCrashLooping"],
            tool_path_trace=None,
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "find_unhealthy_pod",
        lambda _req: type(
            "UnhealthyPodResponseStub",
            (),
            {
                "candidate": type(
                    "CandidateStub",
                    (),
                    {
                        "target": "pod/crashy-abc123",
                        "namespace": "operator-smoke",
                        "kind": "pod",
                        "name": "crashy-abc123",
                        "phase": "Running",
                        "reason": "CrashLoopBackOff",
                        "restart_count": 3,
                        "ready": False,
                        "summary": "Crash looping",
                    },
                )()
            },
        )(),
    )

    report = run_orchestrated_investigation(
        InvestigationReportRequest(
            cluster=incident.cluster,
            namespace=incident.namespace,
            target=incident.target,
            profile=incident.profile,
            lookback_minutes=incident.lookback_minutes,
            alertname=incident.alertname,
            labels=incident.labels,
            annotations=incident.annotations,
        )
    )

    assert report.target == "deployment/crashy"
    assert any("Resolved concrete crash-looping pod: pod/crashy-abc123" in item for item in report.evidence)


def test_run_orchestrated_investigation_preserves_render_request_fields(monkeypatch) -> None:
    incident = _incident()
    captured = {}

    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context(active_batch_id=None))

    def fake_render(req, _execution_context):
        captured["req"] = req
        return InvestigationReport(
            cluster="erauner-home",
            scope="workload",
            target="deployment/crashy",
            diagnosis="Crash Loop Detected",
            confidence="high",
            evidence=["evidence"],
            evidence_items=[],
            related_data=[],
            related_data_note="No meaningful correlated changes found in the requested time window.",
            limitations=[],
            recommended_next_step="next",
            suggested_follow_ups=[],
            guidelines=[],
            normalization_notes=[],
            tool_path_trace=None,
        )

    monkeypatch.setattr(entrypoint, "render_report", fake_render)
    monkeypatch.setattr(
        entrypoint,
        "find_unhealthy_pod",
        lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})(),
    )

    run_orchestrated_investigation(
        InvestigationReportRequest(
            cluster=incident.cluster,
            namespace=incident.namespace,
            target=incident.target,
            profile=incident.profile,
            lookback_minutes=incident.lookback_minutes,
            alertname=incident.alertname,
            labels=incident.labels,
            annotations=incident.annotations,
            include_related_data=False,
            correlation_window_minutes=123,
            correlation_limit=7,
            anchor_timestamp="2026-03-09T12:00:00Z",
        )
    )

    preserved = captured["req"]
    assert preserved.include_related_data is False
    assert preserved.correlation_window_minutes == 123
    assert preserved.correlation_limit == 7
    assert preserved.anchor_timestamp == "2026-03-09T12:00:00Z"


def test_run_orchestrated_investigation_persists_graph_state_with_checkpointer(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    checkpoint_config = GraphCheckpointConfig(
        thread_id="test-orchestrator-thread",
    )

    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context(active_batch_id=None))
    monkeypatch.setattr(
        entrypoint,
        "render_report",
        lambda *_args, **_kwargs: InvestigationReport(
            cluster="erauner-home",
            scope="workload",
            target="deployment/crashy",
            diagnosis="Crash Loop Detected",
            confidence="high",
            evidence=["evidence"],
            evidence_items=[],
            related_data=[],
            related_data_note="No meaningful correlated changes found in the requested time window.",
            limitations=[],
            recommended_next_step="next",
            suggested_follow_ups=[],
            guidelines=[],
            normalization_notes=[],
            tool_path_trace=None,
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "find_unhealthy_pod",
        lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})(),
    )

    report = entrypoint._run_orchestrated_investigation_graph(
        InvestigationReportRequest(
            cluster=incident.cluster,
            namespace=incident.namespace,
            target=incident.target,
            profile=incident.profile,
            lookback_minutes=incident.lookback_minutes,
            alertname=incident.alertname,
            labels=incident.labels,
            annotations=incident.annotations,
        ),
        checkpointer=checkpointer,
        checkpoint_config=checkpoint_config,
    )

    snapshot = get_investigation_graph_state(
        deps=entrypoint._runtime_deps(),
        checkpointer=checkpointer,
        checkpoint_config=checkpoint_config,
    )

    assert report.target == "deployment/crashy"
    assert snapshot.config["configurable"]["thread_id"] == "test-orchestrator-thread"
    assert snapshot.values["execution_context"].updated_plan.active_batch_id is None
    assert snapshot.values["final_report"].target == "deployment/crashy"


def test_internal_graph_runner_rejects_checkpoint_config_without_checkpointer(monkeypatch) -> None:
    incident = _incident()
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context(active_batch_id=None))
    monkeypatch.setattr(
        entrypoint,
        "render_report",
        lambda *_args, **_kwargs: InvestigationReport(
            cluster="erauner-home",
            scope="workload",
            target="deployment/crashy",
            diagnosis="Crash Loop Detected",
            confidence="high",
            evidence=["evidence"],
            evidence_items=[],
            related_data=[],
            related_data_note="No meaningful correlated changes found in the requested time window.",
            limitations=[],
            recommended_next_step="next",
            suggested_follow_ups=[],
            guidelines=[],
            normalization_notes=[],
            tool_path_trace=None,
        ),
    )

    with pytest.raises(ValueError, match="checkpoint_config requires a checkpointer"):
        entrypoint._run_orchestrated_investigation_graph(
            InvestigationReportRequest(
                cluster=incident.cluster,
                namespace=incident.namespace,
                target=incident.target,
                profile=incident.profile,
                lookback_minutes=incident.lookback_minutes,
                alertname=incident.alertname,
                labels=incident.labels,
                annotations=incident.annotations,
            ),
            checkpoint_config=GraphCheckpointConfig(thread_id="test-thread"),
        )


def test_internal_graph_runner_rejects_checkpointer_without_explicit_thread_id(monkeypatch) -> None:
    incident = _incident()
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context(active_batch_id=None))
    monkeypatch.setattr(
        entrypoint,
        "render_report",
        lambda *_args, **_kwargs: InvestigationReport(
            cluster="erauner-home",
            scope="workload",
            target="deployment/crashy",
            diagnosis="Crash Loop Detected",
            confidence="high",
            evidence=["evidence"],
            evidence_items=[],
            related_data=[],
            related_data_note="No meaningful correlated changes found in the requested time window.",
            limitations=[],
            recommended_next_step="next",
            suggested_follow_ups=[],
            guidelines=[],
            normalization_notes=[],
            tool_path_trace=None,
        ),
    )

    with pytest.raises(ValueError, match="explicit thread_id is required"):
        entrypoint._run_orchestrated_investigation_graph(
            InvestigationReportRequest(
                cluster=incident.cluster,
                namespace=incident.namespace,
                target=incident.target,
                profile=incident.profile,
                lookback_minutes=incident.lookback_minutes,
                alertname=incident.alertname,
                labels=incident.labels,
                annotations=incident.annotations,
            ),
            checkpointer=create_in_memory_checkpointer(),
        )


def test_internal_graph_runner_rejects_empty_thread_id_with_checkpointer(monkeypatch) -> None:
    incident = _incident()
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context(active_batch_id=None))
    monkeypatch.setattr(
        entrypoint,
        "render_report",
        lambda *_args, **_kwargs: InvestigationReport(
            cluster="erauner-home",
            scope="workload",
            target="deployment/crashy",
            diagnosis="Crash Loop Detected",
            confidence="high",
            evidence=["evidence"],
            evidence_items=[],
            related_data=[],
            related_data_note="No meaningful correlated changes found in the requested time window.",
            limitations=[],
            recommended_next_step="next",
            suggested_follow_ups=[],
            guidelines=[],
            normalization_notes=[],
            tool_path_trace=None,
        ),
    )

    with pytest.raises(ValueError, match="thread_id is required"):
        entrypoint._run_orchestrated_investigation_graph(
            InvestigationReportRequest(
                cluster=incident.cluster,
                namespace=incident.namespace,
                target=incident.target,
                profile=incident.profile,
                lookback_minutes=incident.lookback_minutes,
                alertname=incident.alertname,
                labels=incident.labels,
                annotations=incident.annotations,
            ),
            checkpointer=create_in_memory_checkpointer(),
            checkpoint_config=GraphCheckpointConfig(),
        )
