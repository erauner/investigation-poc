from investigation_orchestrator.entrypoint import run_orchestrated_investigation
from investigation_orchestrator.checkpointing import GraphCheckpointConfig, create_in_memory_checkpointer
from investigation_orchestrator.graph import get_investigation_graph_state, update_investigation_graph_state
from investigation_orchestrator.evidence_runner import ExternalStepCollectionResult
from investigation_orchestrator.runtime_logging import summarize_graph_state
from investigation_orchestrator.state import PendingExplorationReview
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


def _external_steps_result(
    *submitted_steps,
    pending_exploration_review=None,
    deferred_external_steps=(),
) -> ExternalStepCollectionResult:
    return ExternalStepCollectionResult(
        submitted_steps=list(submitted_steps),
        pending_exploration_review=pending_exploration_review,
        deferred_external_steps=tuple(deferred_external_steps),
    )


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


def _report(target: str = "deployment/crashy") -> InvestigationReport:
    return InvestigationReport(
        cluster="erauner-home",
        scope="workload",
        target=target,
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
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: _external_steps_result(
            SubmittedStepArtifact(
                step_id="collect-target-evidence",
                actual_route={
                    "source_kind": "peer_mcp",
                    "mcp_server": "kubernetes-mcp-server",
                    "tool_name": "pods_log",
                    "tool_path": ["kubernetes-mcp-server", "pods_log"],
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
        ),
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
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: _external_steps_result(
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
        ),
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


def test_run_orchestrated_investigation_forwards_service_peer_failure_metadata(monkeypatch) -> None:
    incident = _incident().model_copy(update={"target": "service/api", "profile": "service", "service_name": "api"})
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
            intent="Collect service evidence",
            subject=InvestigationSubject(
                source="manual",
                kind="target",
                summary="Investigate service/api",
                requested_target="service/api",
                alertname=None,
            ),
            canonical_target=InvestigationTarget(
                source="manual",
                scope="service",
                cluster="erauner-home",
                namespace="operator-smoke",
                requested_target="service/api",
                target="service/api",
                service_name="api",
                node_name=None,
                profile="service",
                lookback_minutes=15,
                normalization_notes=[],
            ),
            steps=[
                EvidenceStepContract(
                    step_id="collect-target-evidence",
                    title="Collect service evidence",
                    plane="service",
                    artifact_type="evidence_bundle",
                    requested_capability="service_evidence_plane",
                    preferred_mcp_server="prometheus-mcp-server",
                    preferred_tool_names=["execute_query"],
                    fallback_mcp_server="kubernetes-mcp-server",
                    fallback_tool_names=["resources_get", "events_list"],
                    execution_mode="external_preferred",
                    execution_inputs=StepExecutionInputs(
                        request_kind="service_context",
                        cluster="erauner-home",
                        namespace="operator-smoke",
                        target="service/api",
                        profile="service",
                        service_name="api",
                        lookback_minutes=15,
                    ),
                )
            ],
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: _external_steps_result(
            SubmittedStepArtifact(
                step_id="collect-target-evidence",
                actual_route={
                    "source_kind": "peer_mcp",
                    "mcp_server": "prometheus-mcp-server",
                    "tool_name": None,
                    "tool_path": ["prometheus-mcp-server"],
                },
                attempted_routes=[
                    {
                        "source_kind": "peer_mcp",
                        "mcp_server": "prometheus-mcp-server",
                        "tool_name": None,
                        "tool_path": ["prometheus-mcp-server"],
                    },
                    {
                        "source_kind": "peer_mcp",
                        "mcp_server": "kubernetes-mcp-server",
                        "tool_name": None,
                        "tool_path": ["kubernetes-mcp-server"],
                    },
                ],
                limitations=["prometheus peer failed: prom down", "kubernetes peer fallback failed: kube down"],
            )
        ),
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
            scope="service",
            target="service/api",
            diagnosis="Service instability detected",
            confidence="medium",
            evidence=["Elevated error rate"],
            evidence_items=[],
            related_data=[],
            related_data_note="No meaningful correlated changes found in the requested time window.",
            limitations=[],
            recommended_next_step="Inspect service metrics and dependent workloads before taking write actions.",
            suggested_follow_ups=[],
            guidelines=[],
            normalization_notes=[],
            tool_path_trace=None,
        ),
    )

    report = run_orchestrated_investigation(
        InvestigationReportRequest(
            cluster=incident.cluster,
            namespace=incident.namespace,
            target=incident.target,
            profile=incident.profile,
            service_name=incident.service_name,
            lookback_minutes=incident.lookback_minutes,
        )
    )

    assert captured["submitted"] is not None
    assert captured["submitted"][0].actual_route.mcp_server == "prometheus-mcp-server"
    assert [route.mcp_server for route in captured["submitted"][0].attempted_routes] == [
        "prometheus-mcp-server",
        "kubernetes-mcp-server",
    ]
    assert "prometheus peer failed: prom down" in captured["submitted"][0].limitations
    assert report.target == "service/api"


def test_run_orchestrated_investigation_forwards_node_peer_failure_metadata(monkeypatch) -> None:
    incident = _incident().model_copy(update={"target": "node/worker3", "node_name": "worker3"})
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
            intent="Collect node evidence",
            subject=InvestigationSubject(
                source="manual",
                kind="target",
                summary="Investigate node/worker3",
                requested_target="node/worker3",
                alertname=None,
            ),
            canonical_target=InvestigationTarget(
                source="manual",
                scope="node",
                cluster="erauner-home",
                namespace=None,
                requested_target="node/worker3",
                target="node/worker3",
                service_name=None,
                node_name="worker3",
                profile="workload",
                lookback_minutes=15,
                normalization_notes=[],
            ),
            steps=[
                EvidenceStepContract(
                    step_id="collect-target-evidence",
                    title="Collect node evidence",
                    plane="node",
                    artifact_type="evidence_bundle",
                    requested_capability="node_evidence_plane",
                    preferred_mcp_server="prometheus-mcp-server",
                    preferred_tool_names=["execute_query"],
                    fallback_mcp_server="kubernetes-mcp-server",
                    fallback_tool_names=["resources_get", "events_list"],
                    execution_mode="external_preferred",
                    execution_inputs=StepExecutionInputs(
                        request_kind="target_context",
                        cluster="erauner-home",
                        target="node/worker3",
                        profile="workload",
                        node_name="worker3",
                        lookback_minutes=15,
                    ),
                )
            ],
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: _external_steps_result(
            SubmittedStepArtifact(
                step_id="collect-target-evidence",
                actual_route={
                    "source_kind": "peer_mcp",
                    "mcp_server": "prometheus-mcp-server",
                    "tool_name": None,
                    "tool_path": ["prometheus-mcp-server"],
                },
                attempted_routes=[
                    {
                        "source_kind": "peer_mcp",
                        "mcp_server": "prometheus-mcp-server",
                        "tool_name": None,
                        "tool_path": ["prometheus-mcp-server"],
                    },
                    {
                        "source_kind": "peer_mcp",
                        "mcp_server": "kubernetes-mcp-server",
                        "tool_name": None,
                        "tool_path": ["kubernetes-mcp-server"],
                    },
                ],
                limitations=["prometheus peer failed: prom down", "kubernetes peer fallback failed: kube down"],
            )
        ),
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
            scope="node",
            target="node/worker3",
            diagnosis="Node pressure detected",
            confidence="medium",
            evidence=["Node memory pressure observed"],
            evidence_items=[],
            related_data=[],
            related_data_note="No meaningful correlated changes found in the requested time window.",
            limitations=[],
            recommended_next_step="Inspect node resource pressure and dependent workloads before taking write actions.",
            suggested_follow_ups=[],
            guidelines=[],
            normalization_notes=[],
            tool_path_trace=None,
        ),
    )

    report = run_orchestrated_investigation(
        InvestigationReportRequest(
            cluster=incident.cluster,
            namespace=incident.namespace,
            target=incident.target,
            profile=incident.profile,
            node_name=incident.node_name,
            lookback_minutes=incident.lookback_minutes,
        )
    )

    assert captured["submitted"] is not None
    assert captured["submitted"][0].actual_route.mcp_server == "prometheus-mcp-server"
    assert [route.mcp_server for route in captured["submitted"][0].attempted_routes] == [
        "prometheus-mcp-server",
        "kubernetes-mcp-server",
    ]
    assert "prometheus peer failed: prom down" in captured["submitted"][0].limitations
    assert report.target == "node/worker3"


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
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())
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
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

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
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

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
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

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


def test_internal_graph_runner_accepts_explicit_thread_id_without_checkpoint_config(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context(active_batch_id=None))
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

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
        thread_id="explicit-thread",
    )

    snapshot = get_investigation_graph_state(
        deps=entrypoint._runtime_deps(),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="explicit-thread"),
    )

    assert report.target == "deployment/crashy"
    assert snapshot.config["configurable"]["thread_id"] == "explicit-thread"


def test_internal_graph_runner_prefers_explicit_thread_id_over_checkpoint_config(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context(active_batch_id=None))
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

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
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="checkpoint-thread"),
        thread_id="override-thread",
    )

    snapshot = get_investigation_graph_state(
        deps=entrypoint._runtime_deps(),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="override-thread"),
    )
    empty_snapshot = get_investigation_graph_state(
        deps=entrypoint._runtime_deps(),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="checkpoint-thread"),
    )

    assert snapshot.values["final_report"].target == "deployment/crashy"
    assert empty_snapshot.values == {}


def test_internal_graph_runner_resumes_after_ensure_context_boundary(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    monkeypatch.setattr(entrypoint, "find_unhealthy_pod", lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})())
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context())
    monkeypatch.setattr(
        entrypoint,
        "get_active_batch",
        lambda *_args, **_kwargs: ActiveEvidenceBatchContract(
            batch_id="batch-1",
            title="Initial evidence",
            intent="Collect workload evidence",
            subject=InvestigationSubject(source="alert", kind="alert", summary="Investigate PodCrashLooping", requested_target="pod/crashy", alertname="PodCrashLooping"),
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
            steps=[],
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: _external_steps_result(),
    )
    monkeypatch.setattr(
        entrypoint,
        "advance_batch",
        lambda *_args, **_kwargs: AdvanceInvestigationRuntimeResponse(
            execution_context=_context(active_batch_id=None),
            next_active_batch=None,
        ),
    )
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

    with pytest.raises(ValueError, match="completed without rendering"):
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
            checkpointer=checkpointer,
            thread_id="resume-after-ensure-context",
            interrupt_after=["ensure_context"],
        )

    snapshot = get_investigation_graph_state(
        deps=entrypoint._runtime_deps(),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="resume-after-ensure-context"),
        interrupt_after=["ensure_context"],
    )

    assert snapshot.next == ("load_active_batch",)
    assert snapshot.values["execution_context"] is not None

    report = entrypoint._resume_orchestrated_investigation_graph(
        checkpointer=checkpointer,
        thread_id="resume-after-ensure-context",
    )

    assert report.target == "deployment/crashy"


def test_internal_graph_runner_resumes_after_external_step_materialization(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    captured = {"submitted": None}
    monkeypatch.setattr(entrypoint, "find_unhealthy_pod", lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})())
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context())
    monkeypatch.setattr(
        entrypoint,
        "get_active_batch",
        lambda *_args, **_kwargs: ActiveEvidenceBatchContract(
            batch_id="batch-1",
            title="Initial evidence",
            intent="Collect workload evidence",
            subject=InvestigationSubject(source="alert", kind="alert", summary="Investigate PodCrashLooping", requested_target="pod/crashy", alertname="PodCrashLooping"),
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
                    preferred_tool_names=["pods_log"],
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
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: _external_steps_result(
            SubmittedStepArtifact(
                step_id="collect-target-evidence",
                actual_route={
                    "source_kind": "peer_mcp",
                    "mcp_server": "kubernetes-mcp-server",
                    "tool_name": "pods_log",
                    "tool_path": ["kubernetes-mcp-server", "pods_log"],
                },
                evidence_bundle={
                    "cluster": "erauner-home",
                    "target": {"namespace": "operator-smoke", "kind": "pod", "name": "crashy-abc123"},
                    "object_state": {"kind": "pod", "name": "crashy-abc123"},
                    "events": [],
                    "log_excerpt": "panic",
                    "metrics": {},
                    "findings": [],
                    "limitations": [],
                    "enrichment_hints": [],
                },
            )
        ),
    )

    def fake_advance(_incident, _execution_context, *, submitted_steps, batch_id=None):
        captured["submitted"] = submitted_steps
        return AdvanceInvestigationRuntimeResponse(
            execution_context=_context(active_batch_id=None),
            next_active_batch=None,
        )

    monkeypatch.setattr(entrypoint, "advance_batch", fake_advance)
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

    with pytest.raises(ValueError, match="completed without rendering"):
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
            checkpointer=checkpointer,
            thread_id="resume-after-external-steps",
            interrupt_after=["collect_external_steps"],
        )

    snapshot = get_investigation_graph_state(
        deps=entrypoint._runtime_deps(),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="resume-after-external-steps"),
        interrupt_after=["collect_external_steps"],
    )

    assert snapshot.next == ("advance_batch",)
    assert len(snapshot.values["submitted_steps"]) == 1

    report = entrypoint._resume_orchestrated_investigation_graph(
        checkpointer=checkpointer,
        thread_id="resume-after-external-steps",
    )

    assert captured["submitted"] is not None
    assert captured["submitted"][0].step_id == "collect-target-evidence"
    assert report.target == "deployment/crashy"


def test_runtime_pauses_for_pending_workload_exploration_review(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    captured = {"submitted": None}
    step = EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect workload evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        requested_capability="workload_evidence_plane",
        preferred_mcp_server="kubernetes-mcp-server",
        preferred_tool_names=["pods_log"],
        fallback_mcp_server=None,
        fallback_tool_names=[],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="target_context",
            cluster="erauner-home",
            namespace="operator-smoke",
            target="deployment/crashy",
            profile="workload",
            lookback_minutes=15,
        ),
    )
    baseline_artifact = SubmittedStepArtifact(
        step_id="collect-target-evidence",
        actual_route={
            "source_kind": "peer_mcp",
            "mcp_server": "kubernetes-mcp-server",
            "tool_name": "pods_log",
            "tool_path": ["kubernetes-mcp-server", "pods_log"],
        },
        evidence_bundle={
            "cluster": "erauner-home",
            "target": {"namespace": "operator-smoke", "kind": "deployment", "name": "crashy"},
            "object_state": {
                "kind": "deployment",
                "name": "crashy",
                "namespace": "operator-smoke",
                "runtimePod": {"name": "crashy-a"},
            },
            "events": [],
            "log_excerpt": "",
            "metrics": {},
            "findings": [],
            "limitations": ["logs unavailable"],
            "enrichment_hints": [],
        },
    )

    monkeypatch.setattr(entrypoint, "find_unhealthy_pod", lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})())
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
                target="deployment/crashy",
                service_name=None,
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=["alertname=PodCrashLooping"],
            ),
            steps=[step],
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: (
            _external_steps_result(
                pending_exploration_review=PendingExplorationReview(
                    batch_id="batch-1",
                    step=step,
                    capability="workload_evidence_plane",
                    baseline_artifact=baseline_artifact,
                    baseline_runtime_pod_name="crashy-a",
                    adequacy_outcome="weak",
                    adequacy_reasons=["logs unavailable"],
                    proposed_probe="Probe one additional runtime pod for deployment/crashy excluding crashy-a.",
                    probe_kind="alternate_runtime_pod",
                )
            )
            if allow_exploration_review
            else _external_steps_result(baseline_artifact)
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "apply_pending_exploration_review",
        lambda review: review.baseline_artifact.model_copy(
            update={
                "evidence_bundle": review.baseline_artifact.evidence_bundle.model_copy(
                    update={
                        "limitations": [
                            *review.baseline_artifact.evidence_bundle.limitations,
                            "bounded workload scout skipped by review decision",
                        ]
                    }
                )
            }
        ),
    )

    def fake_advance(_incident, _execution_context, *, submitted_steps, batch_id=None):
        captured["submitted"] = submitted_steps
        return AdvanceInvestigationRuntimeResponse(
            execution_context=_context(active_batch_id=None),
            next_active_batch=None,
        )

    monkeypatch.setattr(entrypoint, "advance_batch", fake_advance)
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

    result = entrypoint.run_orchestrated_investigation_runtime(
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
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="review-pause-thread",
            enable_exploration_review=True,
        ),
    )

    assert result.status == "interrupted"
    assert result.next_nodes == ("apply_exploration_review",)
    assert result.state["pending_exploration_review"] is not None
    assert result.state["pending_exploration_review"].decision is None
    assert result.state["pending_exploration_review"].probe_kind == "alternate_runtime_pod"
    assert summarize_graph_state(result.state)["pending_review_probe_kind"] == "alternate_runtime_pod"
    assert summarize_graph_state(result.state)["pending_review_stop_reason"] == "awaiting_review"
    assert summarize_graph_state(result.state)["pending_review_step_id_token"] is not None

    updated_state = entrypoint._apply_exploration_review_decision(
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="review-pause-thread",
            enable_exploration_review=True,
        ),
        decision="skip",
    )

    assert updated_state["pending_exploration_review"] is not None
    assert updated_state["pending_exploration_review"].decision == "skip"

    resumed = entrypoint.resume_orchestrated_investigation_runtime(
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="review-pause-thread",
            enable_exploration_review=True,
        )
    )

    assert resumed.status == "completed"
    assert captured["submitted"] is not None
    assert captured["submitted"][0].step_id == "collect-target-evidence"
    assert "bounded workload scout skipped by review decision" in captured["submitted"][0].evidence_bundle.limitations


def test_runtime_with_checkpointing_does_not_enable_review_without_flag(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    step = EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect workload evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        requested_capability="workload_evidence_plane",
        preferred_mcp_server="kubernetes-mcp-server",
        preferred_tool_names=["pods_log"],
        fallback_mcp_server=None,
        fallback_tool_names=[],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="target_context",
            cluster="erauner-home",
            namespace="operator-smoke",
            target="deployment/crashy",
            profile="workload",
            lookback_minutes=15,
        ),
    )
    baseline_artifact = SubmittedStepArtifact(
        step_id="collect-target-evidence",
        actual_route={
            "source_kind": "peer_mcp",
            "mcp_server": "kubernetes-mcp-server",
            "tool_name": "pods_log",
            "tool_path": ["kubernetes-mcp-server", "pods_log"],
        },
        evidence_bundle={
            "cluster": "erauner-home",
            "target": {"namespace": "operator-smoke", "kind": "deployment", "name": "crashy"},
            "object_state": {"kind": "deployment", "name": "crashy", "namespace": "operator-smoke"},
            "events": [],
            "log_excerpt": "",
            "metrics": {},
            "findings": [],
            "limitations": ["logs unavailable"],
            "enrichment_hints": [],
        },
    )

    monkeypatch.setattr(entrypoint, "find_unhealthy_pod", lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})())
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
                target="deployment/crashy",
                service_name=None,
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=[],
            ),
            steps=[step],
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: (
            _external_steps_result(
                pending_exploration_review=PendingExplorationReview(
                    batch_id="batch-1",
                    step=step,
                    capability="workload_evidence_plane",
                    baseline_artifact=baseline_artifact,
                    baseline_runtime_pod_name="crashy-a",
                    adequacy_outcome="weak",
                    adequacy_reasons=["logs unavailable"],
                    proposed_probe="Probe one additional runtime pod for deployment/crashy excluding crashy-a.",
                    probe_kind="alternate_runtime_pod",
                )
            )
            if allow_exploration_review
            else _external_steps_result(baseline_artifact)
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "advance_batch",
        lambda *_args, **_kwargs: AdvanceInvestigationRuntimeResponse(
            execution_context=_context(active_batch_id=None),
            next_active_batch=None,
        ),
    )
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

    result = entrypoint.run_orchestrated_investigation_runtime(
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
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="checkpoint-only-thread",
        ),
    )

    assert result.status == "completed"
    assert result.next_nodes == ()


def test_apply_review_decision_rejects_stale_review_fingerprint(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    step = EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect workload evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        requested_capability="workload_evidence_plane",
        preferred_mcp_server="kubernetes-mcp-server",
        preferred_tool_names=["pods_log"],
        fallback_mcp_server=None,
        fallback_tool_names=[],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="target_context",
            cluster="erauner-home",
            namespace="operator-smoke",
            target="deployment/crashy",
            profile="workload",
            lookback_minutes=15,
        ),
    )
    baseline_artifact = SubmittedStepArtifact(
        step_id="collect-target-evidence",
        actual_route={
            "source_kind": "peer_mcp",
            "mcp_server": "kubernetes-mcp-server",
            "tool_name": "pods_log",
            "tool_path": ["kubernetes-mcp-server", "pods_log"],
        },
        evidence_bundle={
            "cluster": "erauner-home",
            "target": {"namespace": "operator-smoke", "kind": "deployment", "name": "crashy"},
            "object_state": {"kind": "deployment", "name": "crashy", "namespace": "operator-smoke"},
            "events": [],
            "log_excerpt": "",
            "metrics": {},
            "findings": [],
            "limitations": ["logs unavailable"],
            "enrichment_hints": [],
        },
    )

    monkeypatch.setattr(entrypoint, "find_unhealthy_pod", lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})())
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
                target="deployment/crashy",
                service_name=None,
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=[],
            ),
            steps=[step],
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: _external_steps_result(
            pending_exploration_review=PendingExplorationReview(
                batch_id="batch-1",
                step=step,
                capability="workload_evidence_plane",
                baseline_artifact=baseline_artifact,
                baseline_runtime_pod_name="crashy-a",
                adequacy_outcome="weak",
                adequacy_reasons=["logs unavailable"],
                proposed_probe="Probe one additional runtime pod for deployment/crashy excluding crashy-a.",
                probe_kind="alternate_runtime_pod",
            )
        ),
    )
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

    result = entrypoint.run_orchestrated_investigation_runtime(
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
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="stale-review-thread",
            enable_exploration_review=True,
        ),
    )
    assert result.status == "interrupted"

    stale_snapshot = get_investigation_graph_state(
        deps=entrypoint._runtime_deps(allow_exploration_review=True),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="stale-review-thread"),
        enable_exploration_review_interrupt=True,
    )
    stale_checkpoint_id = stale_snapshot.config["configurable"]["checkpoint_id"]
    pending_review = stale_snapshot.values["pending_exploration_review"]
    assert pending_review is not None

    update_investigation_graph_state(
        deps=entrypoint._runtime_deps(allow_exploration_review=True),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="stale-review-thread"),
        values={
            "pending_exploration_review": pending_review.model_copy(
                update={"proposed_probe": "Probe one additional runtime pod for deployment/crashy excluding crashy-b."}
            )
        },
        as_node="prepare_exploration_review",
        enable_exploration_review_interrupt=True,
    )

    with pytest.raises(ValueError, match="state has changed"):
        entrypoint._apply_exploration_review_decision(
            runtime=entrypoint.OrchestratorRuntimeConfig(
                checkpointer=checkpointer,
                thread_id="stale-review-thread",
                checkpoint_id=stale_checkpoint_id,
                enable_exploration_review=True,
            ),
            decision="skip",
        )


def test_resume_before_review_decision_fails_clearly(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    step = EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect workload evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        requested_capability="workload_evidence_plane",
        preferred_mcp_server="kubernetes-mcp-server",
        preferred_tool_names=["pods_log"],
        fallback_mcp_server=None,
        fallback_tool_names=[],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="target_context",
            cluster="erauner-home",
            namespace="operator-smoke",
            target="deployment/crashy",
            profile="workload",
            lookback_minutes=15,
        ),
    )
    baseline_artifact = SubmittedStepArtifact(
        step_id="collect-target-evidence",
        actual_route={
            "source_kind": "peer_mcp",
            "mcp_server": "kubernetes-mcp-server",
            "tool_name": "pods_log",
            "tool_path": ["kubernetes-mcp-server", "pods_log"],
        },
        evidence_bundle={
            "cluster": "erauner-home",
            "target": {"namespace": "operator-smoke", "kind": "deployment", "name": "crashy"},
            "object_state": {"kind": "deployment", "name": "crashy", "namespace": "operator-smoke"},
            "events": [],
            "log_excerpt": "",
            "metrics": {},
            "findings": [],
            "limitations": ["logs unavailable"],
            "enrichment_hints": [],
        },
    )

    monkeypatch.setattr(entrypoint, "find_unhealthy_pod", lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})())
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
                target="deployment/crashy",
                service_name=None,
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=[],
            ),
            steps=[step],
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: (
            _external_steps_result(
                pending_exploration_review=PendingExplorationReview(
                    batch_id="batch-1",
                    step=step,
                    capability="workload_evidence_plane",
                    baseline_artifact=baseline_artifact,
                    baseline_runtime_pod_name="crashy-a",
                    adequacy_outcome="weak",
                    adequacy_reasons=["logs unavailable"],
                    proposed_probe="Probe one additional runtime pod for deployment/crashy excluding crashy-a.",
                )
            )
            if allow_exploration_review
            else _external_steps_result(baseline_artifact)
        ),
    )
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

    result = entrypoint.run_orchestrated_investigation_runtime(
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
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="awaiting-review-thread",
            enable_exploration_review=True,
        ),
    )

    assert result.status == "interrupted"
    with pytest.raises(ValueError, match="awaiting decision"):
        entrypoint.resume_orchestrated_investigation_runtime(
            runtime=entrypoint.OrchestratorRuntimeConfig(
                checkpointer=checkpointer,
                thread_id="awaiting-review-thread",
                enable_exploration_review=True,
            )
        )


def test_apply_review_decision_targets_latest_thread_head(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    step = EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect workload evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        requested_capability="workload_evidence_plane",
        preferred_mcp_server="kubernetes-mcp-server",
        preferred_tool_names=["pods_log"],
        fallback_mcp_server=None,
        fallback_tool_names=[],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="target_context",
            cluster="erauner-home",
            namespace="operator-smoke",
            target="deployment/crashy",
            profile="workload",
            lookback_minutes=15,
        ),
    )
    baseline_artifact = SubmittedStepArtifact(
        step_id="collect-target-evidence",
        actual_route={
            "source_kind": "peer_mcp",
            "mcp_server": "kubernetes-mcp-server",
            "tool_name": "pods_log",
            "tool_path": ["kubernetes-mcp-server", "pods_log"],
        },
        evidence_bundle={
            "cluster": "erauner-home",
            "target": {"namespace": "operator-smoke", "kind": "deployment", "name": "crashy"},
            "object_state": {"kind": "deployment", "name": "crashy", "namespace": "operator-smoke"},
            "events": [],
            "log_excerpt": "",
            "metrics": {},
            "findings": [],
            "limitations": ["logs unavailable"],
            "enrichment_hints": [],
        },
    )

    monkeypatch.setattr(entrypoint, "find_unhealthy_pod", lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})())
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
                target="deployment/crashy",
                service_name=None,
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=[],
            ),
            steps=[step],
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: (
            _external_steps_result(
                pending_exploration_review=PendingExplorationReview(
                    batch_id="batch-1",
                    step=step,
                    capability="workload_evidence_plane",
                    baseline_artifact=baseline_artifact,
                    baseline_runtime_pod_name="crashy-a",
                    adequacy_outcome="weak",
                    adequacy_reasons=["logs unavailable"],
                    proposed_probe="Probe one additional runtime pod for deployment/crashy excluding crashy-a.",
                )
            )
            if allow_exploration_review
            else _external_steps_result(baseline_artifact)
        ),
    )

    result = entrypoint.run_orchestrated_investigation_runtime(
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
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="pinned-review-thread",
            enable_exploration_review=True,
        ),
    )
    assert result.status == "interrupted"

    original_head_snapshot = get_investigation_graph_state(
        deps=entrypoint._runtime_deps(allow_exploration_review=True),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="pinned-review-thread"),
        enable_exploration_review_interrupt=True,
    )
    pinned_checkpoint_id = original_head_snapshot.config["configurable"]["checkpoint_id"]

    update_investigation_graph_state(
        deps=entrypoint._runtime_deps(allow_exploration_review=True),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="pinned-review-thread"),
        values={"submitted_steps": []},
        as_node="prepare_exploration_review",
        enable_exploration_review_interrupt=True,
    )

    latest_head_snapshot = get_investigation_graph_state(
        deps=entrypoint._runtime_deps(allow_exploration_review=True),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="pinned-review-thread"),
        enable_exploration_review_interrupt=True,
    )
    assert latest_head_snapshot.config["configurable"]["checkpoint_id"] != pinned_checkpoint_id

    updated_state = entrypoint._apply_exploration_review_decision(
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="pinned-review-thread",
            checkpoint_id=pinned_checkpoint_id,
            enable_exploration_review=True,
        ),
        decision="skip",
    )

    assert updated_state["pending_exploration_review"] is not None
    assert updated_state["pending_exploration_review"].decision == "skip"


def test_apply_review_decision_rejects_overwrite_and_preserves_original_decision(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    step = EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect workload evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        requested_capability="workload_evidence_plane",
        preferred_mcp_server="kubernetes-mcp-server",
        preferred_tool_names=["pods_log"],
        fallback_mcp_server=None,
        fallback_tool_names=[],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="target_context",
            cluster="erauner-home",
            namespace="operator-smoke",
            target="deployment/crashy",
            profile="workload",
            lookback_minutes=15,
        ),
    )
    baseline_artifact = SubmittedStepArtifact(
        step_id="collect-target-evidence",
        actual_route={
            "source_kind": "peer_mcp",
            "mcp_server": "kubernetes-mcp-server",
            "tool_name": "pods_log",
            "tool_path": ["kubernetes-mcp-server", "pods_log"],
        },
        evidence_bundle={
            "cluster": "erauner-home",
            "target": {"namespace": "operator-smoke", "kind": "deployment", "name": "crashy"},
            "object_state": {"kind": "deployment", "name": "crashy", "namespace": "operator-smoke"},
            "events": [],
            "log_excerpt": "",
            "metrics": {},
            "findings": [],
            "limitations": ["logs unavailable"],
            "enrichment_hints": [],
        },
    )

    monkeypatch.setattr(entrypoint, "find_unhealthy_pod", lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})())
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
                target="deployment/crashy",
                service_name=None,
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=[],
            ),
            steps=[step],
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: _external_steps_result(
            pending_exploration_review=PendingExplorationReview(
                batch_id="batch-1",
                step=step,
                capability="workload_evidence_plane",
                baseline_artifact=baseline_artifact,
                baseline_runtime_pod_name="crashy-a",
                adequacy_outcome="weak",
                adequacy_reasons=["logs unavailable"],
                proposed_probe="Probe one additional runtime pod for deployment/crashy excluding crashy-a.",
            )
        ),
    )
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

    result = entrypoint.run_orchestrated_investigation_runtime(
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
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="decision-overwrite-thread",
            enable_exploration_review=True,
        ),
    )
    assert result.status == "interrupted"

    original_head_snapshot = get_investigation_graph_state(
        deps=entrypoint._runtime_deps(allow_exploration_review=True),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="decision-overwrite-thread"),
        enable_exploration_review_interrupt=True,
    )
    stale_checkpoint_id = original_head_snapshot.config["configurable"]["checkpoint_id"]

    entrypoint._apply_exploration_review_decision(
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="decision-overwrite-thread",
            enable_exploration_review=True,
        ),
        decision="skip",
        )

    with pytest.raises(ValueError, match="already been recorded"):
        entrypoint._apply_exploration_review_decision(
            runtime=entrypoint.OrchestratorRuntimeConfig(
                checkpointer=checkpointer,
                thread_id="decision-overwrite-thread",
                checkpoint_id=stale_checkpoint_id,
                enable_exploration_review=True,
            ),
            decision="approve",
        )

    latest_state = get_investigation_graph_state(
        deps=entrypoint._runtime_deps(allow_exploration_review=True),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="decision-overwrite-thread"),
        enable_exploration_review_interrupt=True,
    ).values
    assert latest_state["pending_exploration_review"] is not None
    assert latest_state["pending_exploration_review"].decision == "skip"


def test_runtime_review_resumes_deferred_external_steps_in_same_batch(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    captured = {"submitted": None}
    workload_step = EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect workload evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        requested_capability="workload_evidence_plane",
        preferred_mcp_server="kubernetes-mcp-server",
        preferred_tool_names=["pods_log"],
        fallback_mcp_server=None,
        fallback_tool_names=[],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="target_context",
            cluster="erauner-home",
            namespace="operator-smoke",
            target="deployment/crashy",
            profile="workload",
            lookback_minutes=15,
        ),
    )
    service_step = EvidenceStepContract(
        step_id="collect-service-evidence",
        title="Collect service evidence",
        plane="service",
        artifact_type="evidence_bundle",
        requested_capability="service_evidence_plane",
        preferred_mcp_server="prometheus-mcp-server",
        preferred_tool_names=["execute_query"],
        fallback_mcp_server="kubernetes-mcp-server",
        fallback_tool_names=["resources_get"],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="service_context",
            cluster="erauner-home",
            namespace="operator-smoke",
            target="service/api",
            profile="service",
            service_name="api",
            lookback_minutes=15,
        ),
    )
    baseline_artifact = SubmittedStepArtifact(
        step_id="collect-target-evidence",
        actual_route={
            "source_kind": "peer_mcp",
            "mcp_server": "kubernetes-mcp-server",
            "tool_name": "pods_log",
            "tool_path": ["kubernetes-mcp-server", "pods_log"],
        },
        evidence_bundle={
            "cluster": "erauner-home",
            "target": {"namespace": "operator-smoke", "kind": "deployment", "name": "crashy"},
            "object_state": {"kind": "deployment", "name": "crashy", "namespace": "operator-smoke"},
            "events": [],
            "log_excerpt": "",
            "metrics": {},
            "findings": [],
            "limitations": ["logs unavailable"],
            "enrichment_hints": [],
        },
    )
    service_artifact = SubmittedStepArtifact(
        step_id="collect-service-evidence",
        actual_route={
            "source_kind": "peer_mcp",
            "mcp_server": "prometheus-mcp-server",
            "tool_name": "execute_query",
            "tool_path": ["prometheus-mcp-server", "execute_query"],
        },
        evidence_bundle={
            "cluster": "erauner-home",
            "target": {"namespace": "operator-smoke", "kind": "service", "name": "api"},
            "object_state": {"kind": "service", "name": "api", "namespace": "operator-smoke"},
            "events": [],
            "log_excerpt": "",
            "metrics": {"service_error_rate": 0.25},
            "findings": [],
            "limitations": [],
            "enrichment_hints": [],
        },
    )

    monkeypatch.setattr(entrypoint, "find_unhealthy_pod", lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})())
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context())
    monkeypatch.setattr(
        entrypoint,
        "get_active_batch",
        lambda *_args, **_kwargs: ActiveEvidenceBatchContract(
            batch_id="batch-1",
            title="Initial evidence",
            intent="Collect workload and service evidence",
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
                target="deployment/crashy",
                service_name="api",
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=[],
            ),
            steps=[workload_step, service_step],
        ),
    )

    def fake_collect(_batch, allow_exploration_review=False, steps=None):
        if steps:
            assert [step.step_id for step in steps] == ["collect-service-evidence"]
            return _external_steps_result(service_artifact)
        assert allow_exploration_review is True
        return _external_steps_result(
            pending_exploration_review=PendingExplorationReview(
                batch_id="batch-1",
                step=workload_step,
                capability="workload_evidence_plane",
                baseline_artifact=baseline_artifact,
                baseline_runtime_pod_name="crashy-a",
                adequacy_outcome="weak",
                adequacy_reasons=["logs unavailable"],
                proposed_probe="Probe one additional runtime pod for deployment/crashy excluding crashy-a.",
            ),
            deferred_external_steps=[service_step],
        )

    monkeypatch.setattr(entrypoint, "collect_external_steps", fake_collect)
    monkeypatch.setattr(
        entrypoint,
        "apply_pending_exploration_review",
        lambda review: review.baseline_artifact.model_copy(
            update={
                "evidence_bundle": review.baseline_artifact.evidence_bundle.model_copy(
                    update={
                        "limitations": [
                            *review.baseline_artifact.evidence_bundle.limitations,
                            "bounded workload scout approved by review decision",
                        ]
                    }
                )
            }
        ),
    )

    def fake_advance(_incident, _execution_context, *, submitted_steps, batch_id=None):
        captured["submitted"] = submitted_steps
        return AdvanceInvestigationRuntimeResponse(
            execution_context=_context(active_batch_id=None),
            next_active_batch=None,
        )

    monkeypatch.setattr(entrypoint, "advance_batch", fake_advance)
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

    result = entrypoint.run_orchestrated_investigation_runtime(
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
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="deferred-steps-thread",
            enable_exploration_review=True,
        ),
    )

    assert result.status == "interrupted"
    assert [step.step_id for step in result.state["deferred_external_steps"]] == ["collect-service-evidence"]

    entrypoint._apply_exploration_review_decision(
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="deferred-steps-thread",
            enable_exploration_review=True,
        ),
        decision="approve",
    )

    resumed = entrypoint.resume_orchestrated_investigation_runtime(
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="deferred-steps-thread",
            enable_exploration_review=True,
        )
    )

    assert resumed.status == "completed"
    assert captured["submitted"] is not None
    assert [item.step_id for item in captured["submitted"]] == [
        "collect-target-evidence",
        "collect-service-evidence",
    ]


def test_runtime_pauses_for_pending_workload_exploration_review_approve(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    captured = {"submitted": None}
    step = EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect workload evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        requested_capability="workload_evidence_plane",
        preferred_mcp_server="kubernetes-mcp-server",
        preferred_tool_names=["pods_log"],
        fallback_mcp_server=None,
        fallback_tool_names=[],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="target_context",
            cluster="erauner-home",
            namespace="operator-smoke",
            target="deployment/crashy",
            profile="workload",
            lookback_minutes=15,
        ),
    )
    baseline_artifact = SubmittedStepArtifact(
        step_id="collect-target-evidence",
        actual_route={
            "source_kind": "peer_mcp",
            "mcp_server": "kubernetes-mcp-server",
            "tool_name": "pods_log",
            "tool_path": ["kubernetes-mcp-server", "pods_log"],
        },
        evidence_bundle={
            "cluster": "erauner-home",
            "target": {"namespace": "operator-smoke", "kind": "deployment", "name": "crashy"},
            "object_state": {"kind": "deployment", "name": "crashy", "namespace": "operator-smoke"},
            "events": [],
            "log_excerpt": "",
            "metrics": {},
            "findings": [],
            "limitations": ["logs unavailable"],
            "enrichment_hints": [],
        },
    )
    monkeypatch.setattr(entrypoint, "find_unhealthy_pod", lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})())
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
                target="deployment/crashy",
                service_name=None,
                node_name=None,
                profile="workload",
                lookback_minutes=15,
                normalization_notes=[],
            ),
            steps=[step],
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: (
            _external_steps_result(
                pending_exploration_review=PendingExplorationReview(
                    batch_id="batch-1",
                    step=step,
                    capability="workload_evidence_plane",
                    baseline_artifact=baseline_artifact,
                    baseline_runtime_pod_name="crashy-a",
                    adequacy_outcome="weak",
                    adequacy_reasons=["logs unavailable"],
                    proposed_probe="Probe one additional runtime pod for deployment/crashy excluding crashy-a.",
                )
            )
            if allow_exploration_review
            else _external_steps_result(baseline_artifact)
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "apply_pending_exploration_review",
        lambda review: review.baseline_artifact.model_copy(
            update={
                "evidence_bundle": review.baseline_artifact.evidence_bundle.model_copy(
                    update={
                        "limitations": [
                            *review.baseline_artifact.evidence_bundle.limitations,
                            "bounded workload scout approved by review decision",
                        ]
                    }
                )
            }
        ),
    )

    def fake_advance(_incident, _execution_context, *, submitted_steps, batch_id=None):
        captured["submitted"] = submitted_steps
        return AdvanceInvestigationRuntimeResponse(
            execution_context=_context(active_batch_id=None),
            next_active_batch=None,
        )

    monkeypatch.setattr(entrypoint, "advance_batch", fake_advance)
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

    result = entrypoint.run_orchestrated_investigation_runtime(
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
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="review-approve-thread",
            enable_exploration_review=True,
        ),
    )

    assert result.status == "interrupted"

    entrypoint._apply_exploration_review_decision(
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="review-approve-thread",
            enable_exploration_review=True,
        ),
        decision="approve",
    )

    resumed = entrypoint.resume_orchestrated_investigation_runtime(
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="review-approve-thread",
            enable_exploration_review=True,
        )
    )

    assert resumed.status == "completed"
    assert captured["submitted"] is not None
    assert "bounded workload scout approved by review decision" in captured["submitted"][0].evidence_bundle.limitations


def test_internal_graph_runner_reads_latest_state_even_with_pinned_checkpoint_id(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    calls = {"get_active_batch": 0}
    monkeypatch.setattr(entrypoint, "find_unhealthy_pod", lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})())
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context())
    monkeypatch.setattr(
        entrypoint,
        "get_active_batch",
        lambda *_args, **_kwargs: calls.__setitem__("get_active_batch", calls["get_active_batch"] + 1) or ActiveEvidenceBatchContract(
            batch_id="batch-1",
            title="Initial evidence",
            intent="Collect workload evidence",
            subject=InvestigationSubject(source="alert", kind="alert", summary="Investigate PodCrashLooping", requested_target="pod/crashy", alertname="PodCrashLooping"),
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
            steps=[],
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "collect_external_steps",
        lambda _batch, allow_exploration_review=False: _external_steps_result(),
    )
    monkeypatch.setattr(
        entrypoint,
        "advance_batch",
        lambda *_args, **_kwargs: AdvanceInvestigationRuntimeResponse(
            execution_context=_context(active_batch_id=None),
            next_active_batch=None,
        ),
    )
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

    with pytest.raises(ValueError, match="completed without rendering"):
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
            checkpointer=checkpointer,
            thread_id="pinned-checkpoint-thread",
            interrupt_after=["ensure_context"],
        )

    head_snapshot = get_investigation_graph_state(
        deps=entrypoint._runtime_deps(),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="pinned-checkpoint-thread"),
    )
    pinned_checkpoint_id = head_snapshot.config["configurable"]["checkpoint_id"]

    update_investigation_graph_state(
        deps=entrypoint._runtime_deps(),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="pinned-checkpoint-thread"),
        values={"execution_context": _context(active_batch_id=None)},
        as_node="ensure_context",
    )

    latest_snapshot = get_investigation_graph_state(
        deps=entrypoint._runtime_deps(),
        checkpointer=checkpointer,
        checkpoint_config=GraphCheckpointConfig(thread_id="pinned-checkpoint-thread"),
    )
    assert latest_snapshot.config["configurable"]["checkpoint_id"] != pinned_checkpoint_id

    report = entrypoint._resume_orchestrated_investigation_graph(
        checkpointer=checkpointer,
        thread_id="pinned-checkpoint-thread",
        checkpoint_id=pinned_checkpoint_id,
    )

    assert calls["get_active_batch"] == 0
    assert report.target == "deployment/crashy"


def test_internal_graph_resume_rejects_missing_state() -> None:
    with pytest.raises(ValueError, match="no resumable graph state exists"):
        entrypoint._resume_orchestrated_investigation_graph(
            checkpointer=create_in_memory_checkpointer(),
            thread_id="missing-thread",
        )


def test_internal_graph_resume_rejects_missing_thread_id() -> None:
    with pytest.raises(ValueError, match="explicit thread_id is required"):
        entrypoint._resume_orchestrated_investigation_graph(
            checkpointer=create_in_memory_checkpointer(),
        )


def test_orchestrator_runtime_logs_are_redacted(monkeypatch, caplog) -> None:
    incident = _incident()
    caplog.set_level("INFO", logger="investigation_orchestrator.runtime")
    monkeypatch.setattr(entrypoint, "find_unhealthy_pod", lambda _req: type("UnhealthyPodResponseStub", (), {"candidate": None})())
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context(active_batch_id=None))
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

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
        checkpointer=create_in_memory_checkpointer(),
        thread_id="log-thread",
    )

    assert report.target == "deployment/crashy"
    assert "orchestrator_graph_run mode=invoke status=start" in caplog.text
    assert "orchestrator_graph_node event=enter node=ensure_context" in caplog.text
    assert '"has_thread_id": true' in caplog.text
    assert '"thread_id_token":' in caplog.text
    assert "log-thread" not in caplog.text
    assert "operator-smoke" not in caplog.text
    assert "PodCrashLooping" not in caplog.text
    assert "batch-1" not in caplog.text
    assert "collect-target-evidence" not in caplog.text


def test_summarize_graph_state_handles_pending_review_without_probe_kind() -> None:
    step = EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect workload evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        requested_capability="workload_evidence_plane",
        preferred_mcp_server="kubernetes-mcp-server",
        preferred_tool_names=["pods_log"],
        fallback_mcp_server=None,
        fallback_tool_names=[],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="target_context",
            cluster="erauner-home",
            namespace="operator-smoke",
            target="deployment/crashy",
            profile="workload",
            lookback_minutes=15,
        ),
    )
    baseline_artifact = SubmittedStepArtifact(
        step_id="collect-target-evidence",
        actual_route={
            "source_kind": "peer_mcp",
            "mcp_server": "kubernetes-mcp-server",
            "tool_name": "pods_log",
            "tool_path": ["kubernetes-mcp-server", "pods_log"],
        },
        evidence_bundle={
            "cluster": "erauner-home",
            "target": {"namespace": "operator-smoke", "kind": "deployment", "name": "crashy"},
            "object_state": {"kind": "deployment", "name": "crashy", "namespace": "operator-smoke"},
            "events": [],
            "log_excerpt": "",
            "metrics": {},
            "findings": [],
            "limitations": ["logs unavailable"],
            "enrichment_hints": [],
        },
    )

    summary = summarize_graph_state(
        {
            "execution_context": _context(),
            "active_batch": None,
            "submitted_steps": [],
            "pending_exploration_review": PendingExplorationReview(
                batch_id="batch-1",
                step=step,
                capability="workload_evidence_plane",
                baseline_artifact=baseline_artifact,
                baseline_runtime_pod_name="crashy-a",
                adequacy_outcome="weak",
                adequacy_reasons=["logs unavailable"],
                proposed_probe="Probe one additional runtime pod for deployment/crashy excluding crashy-a.",
            ),
            "remaining_batch_budget": 1,
            "final_report": None,
        }
    )

    assert summary["pending_review_probe_kind"] is None
    assert summary["pending_review_stop_reason"] == "awaiting_review"


def test_internal_graph_resume_applies_pod_compatibility_when_request_is_provided(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
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

    with pytest.raises(ValueError, match="completed without rendering"):
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
            checkpointer=checkpointer,
            thread_id="resume-pod-compat",
            interrupt_after=["ensure_context"],
        )

    report = entrypoint._resume_orchestrated_investigation_graph(
        checkpointer=checkpointer,
        req=InvestigationReportRequest(
            cluster=incident.cluster,
            namespace=incident.namespace,
            target=incident.target,
            profile=incident.profile,
            lookback_minutes=incident.lookback_minutes,
            alertname=incident.alertname,
            labels=incident.labels,
            annotations=incident.annotations,
        ),
        thread_id="resume-pod-compat",
    )

    assert any("Resolved concrete crash-looping pod: pod/crashy-abc123" in item for item in report.evidence)


def test_runtime_api_returns_completed_result(monkeypatch) -> None:
    incident = _incident()
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context(active_batch_id=None))
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

    result = entrypoint.run_orchestrated_investigation_runtime(
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
    )

    assert result.status == "completed"
    assert result.final_report is not None
    assert result.final_report.target == "deployment/crashy"
    assert result.next_nodes == ()


def test_runtime_api_returns_interrupted_result(monkeypatch) -> None:
    incident = _incident()
    checkpointer = create_in_memory_checkpointer()
    monkeypatch.setattr(entrypoint, "seed_context", lambda *_args, **_kwargs: _context())
    monkeypatch.setattr(entrypoint, "render_report", lambda *_args, **_kwargs: _report())

    result = entrypoint.run_orchestrated_investigation_runtime(
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
        runtime=entrypoint.OrchestratorRuntimeConfig(
            checkpointer=checkpointer,
            thread_id="runtime-api-interrupted",
            interrupt_after=["ensure_context"],
        ),
    )

    assert result.status == "interrupted"
    assert result.final_report is None
    assert result.next_nodes == ("load_active_batch",)
