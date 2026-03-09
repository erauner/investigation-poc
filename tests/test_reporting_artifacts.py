from investigation_service.models import (
    ActualRoute,
    AdvanceInvestigationRuntimeRequest,
    HandoffActiveEvidenceBatchRequest,
    BuildInvestigationPlanRequest,
    CorrelatedChange,
    CorrelatedChangesResponse,
    EvidenceBatch,
    EvidenceBatchExecution,
    EvidenceBundle,
    Finding,
    Hypothesis,
    InvestigationAnalysis,
    InvestigationPlan,
    InvestigationReportingRequest,
    InvestigationReportRequest,
    InvestigationTarget,
    PlanStep,
    ReportingExecutionContext,
    StepRouteProvenance,
    StepArtifact,
    SubmittedStepArtifact,
    TargetRef,
)
from investigation_service import reporting


def _target() -> InvestigationTarget:
    return InvestigationTarget(
        source="manual",
        scope="service",
        cluster="artifact-cluster",
        namespace="artifact-ns",
        requested_target="service/api",
        target="service/api-resolved",
        node_name=None,
        service_name="api-resolved",
        profile="service",
        lookback_minutes=15,
        normalization_notes=["artifact-note"],
    )


def _plan() -> InvestigationPlan:
    return InvestigationPlan(
        mode="targeted_rca",
        objective="Investigate service/api",
        target=_target(),
        steps=[
            PlanStep(
                id="collect-target-evidence",
                title="Collect service evidence",
                category="evidence",
                plane="service",
                rationale="Collect target evidence",
                suggested_capability="service_evidence_plane",
            ),
            PlanStep(
                id="collect-change-candidates",
                title="Collect change candidates",
                category="evidence",
                plane="changes",
                rationale="Collect change candidates",
                suggested_capability="collect_change_candidates",
            ),
        ],
        evidence_batches=[],
        active_batch_id="batch-1",
        planning_notes=["artifact-note"],
    )


def _runtime_plan() -> InvestigationPlan:
    return _plan().model_copy(
        update={
            "evidence_batches": [
                EvidenceBatch(
                    id="batch-1",
                    title="Initial evidence",
                    status="pending",
                    intent="Collect target evidence.",
                    step_ids=["collect-target-evidence", "collect-change-candidates"],
                )
            ]
        }
    )


def _bundle() -> EvidenceBundle:
    return EvidenceBundle(
        cluster="artifact-cluster",
        target=TargetRef(namespace="artifact-ns", kind="service", name="api-resolved"),
        object_state={"kind": "service", "name": "api-resolved"},
        events=[],
        log_excerpt="",
        metrics={},
        findings=[
            Finding(
                severity="warning",
                source="heuristic",
                title="Service instability",
                evidence="Observed instability in service signals",
            )
        ],
        limitations=[],
        enrichment_hints=[],
    )


def _execution(include_changes: bool = False) -> EvidenceBatchExecution:
    artifacts = [
        StepArtifact(
            step_id="collect-target-evidence",
            plane="service",
            artifact_type="evidence_bundle",
            summary=["Service instability"],
            limitations=[],
            evidence_bundle=_bundle(),
            route_provenance=StepRouteProvenance(
                requested_capability="service_evidence_plane",
                route_satisfaction="unmatched",
                actual_route=ActualRoute(
                    source_kind="investigation_internal",
                    mcp_server="investigation-mcp-server",
                    tool_name="collect_service_evidence",
                    tool_path=["planner._execute_step", "deps.collect_service_evidence"],
                ),
            ),
        )
    ]
    if include_changes:
        artifacts.append(
            StepArtifact(
                step_id="collect-change-candidates",
                plane="changes",
                artifact_type="change_candidates",
                summary=["Deployment rollout"],
                limitations=[],
                change_candidates=CorrelatedChangesResponse(
                    cluster="artifact-cluster",
                    scope="service",
                    target="service/api-resolved",
                    changes=[
                        CorrelatedChange(
                            fingerprint="rollout|deployment|artifact-ns|api",
                            timestamp="2026-03-08T10:00:00Z",
                            source="rollout",
                            resource_kind="Deployment",
                            namespace="artifact-ns",
                            name="api",
                            relation="same_service",
                            summary="Deployment rollout",
                            confidence="medium",
                        )
                    ],
                    limitations=[],
                ),
                route_provenance=StepRouteProvenance(
                    requested_capability="collect_change_candidates",
                    route_satisfaction="preferred",
                    actual_route=ActualRoute(
                        source_kind="investigation_internal",
                        mcp_server="investigation-mcp-server",
                        tool_name="collect_change_candidates",
                        tool_path=["planner._execute_step", "deps.collect_change_candidates"],
                    ),
                ),
            )
        )
    return EvidenceBatchExecution(
        batch_id="batch-1",
        executed_step_ids=[artifact.step_id for artifact in artifacts],
        artifacts=artifacts,
        execution_notes=["executed batch-1"],
    )


def _alert_execution() -> EvidenceBatchExecution:
    return EvidenceBatchExecution(
        batch_id="batch-1",
        executed_step_ids=["collect-alert-evidence", "collect-target-evidence"],
        artifacts=[
            StepArtifact(
                step_id="collect-alert-evidence",
                plane="alert",
                artifact_type="evidence_bundle",
                summary=[
                    "Alert PodCrashLooping requested pod/crashy",
                    "Resolved runtime target: pod/crashy-abc123",
                    "Alert fired",
                ],
                limitations=[],
                evidence_bundle=EvidenceBundle(
                    cluster="artifact-cluster",
                    target=TargetRef(namespace="artifact-ns", kind="pod", name="crashy-abc123"),
                    object_state={"kind": "pod", "name": "crashy-abc123"},
                    events=[],
                    log_excerpt="",
                    metrics={},
                    findings=[
                        Finding(
                            severity="warning",
                            source="events",
                            title="Alert fired",
                            evidence="Alert PodCrashLooping fired for pod/crashy-abc123",
                        )
                    ],
                    limitations=[],
                    enrichment_hints=[],
                ),
                route_provenance=StepRouteProvenance(
                    requested_capability="alert_evidence_plane",
                    route_satisfaction="unmatched",
                    actual_route=ActualRoute(
                        source_kind="investigation_internal",
                        mcp_server="investigation-mcp-server",
                        tool_name="collect_alert_evidence",
                        tool_path=["planner._execute_step", "deps.collect_alert_evidence"],
                    ),
                ),
            ),
            StepArtifact(
                step_id="collect-target-evidence",
                plane="workload",
                artifact_type="evidence_bundle",
                summary=["Crash Loop Detected"],
                limitations=[],
                evidence_bundle=EvidenceBundle(
                    cluster="artifact-cluster",
                    target=TargetRef(namespace="artifact-ns", kind="pod", name="crashy-abc123"),
                    object_state={"kind": "pod", "name": "crashy-abc123"},
                    events=[],
                    log_excerpt="",
                    metrics={},
                    findings=[
                        Finding(
                            severity="critical",
                            source="events",
                            title="Crash Loop Detected",
                            evidence="BackOff restarting failed container",
                        )
                    ],
                    limitations=[],
                    enrichment_hints=[],
                ),
                route_provenance=StepRouteProvenance(
                    requested_capability="workload_evidence_plane",
                    route_satisfaction="unmatched",
                    actual_route=ActualRoute(
                        source_kind="investigation_internal",
                        mcp_server="investigation-mcp-server",
                        tool_name="collect_workload_evidence",
                        tool_path=["planner._execute_step", "deps.collect_workload_evidence"],
                    ),
                ),
            ),
        ],
        execution_notes=["executed batch-1"],
    )


def test_render_investigation_report_prefers_plan_target_fields(monkeypatch) -> None:
    correlation_request = {}

    monkeypatch.setattr(reporting, "build_investigation_plan", lambda req: _plan())
    monkeypatch.setattr(reporting, "execute_investigation_step", lambda req: _execution())
    monkeypatch.setattr(
        reporting,
        "update_investigation_plan",
        lambda req: req.plan.model_copy(update={"active_batch_id": None}),
    )
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))
    monkeypatch.setattr(
        reporting,
        "collect_correlated_changes_for_target",
        lambda target, **kwargs: (
            correlation_request.setdefault("target", target),
            CorrelatedChangesResponse(
                cluster=target.cluster or "artifact-cluster",
                scope=target.profile,
                target=target.target,
                changes=[],
                limitations=[],
            ),
        )[1],
    )

    report = reporting.render_investigation_report(
        InvestigationReportRequest(target="service/api", profile="service", include_related_data=True)
    )

    assert report.normalization_notes == [
        "artifact-note",
        "cluster resolved from collected context: artifact-cluster",
    ]
    assert correlation_request["target"].cluster == "artifact-cluster"
    assert correlation_request["target"].namespace == "artifact-ns"
    assert correlation_request["target"].target == "service/api-resolved"


def test_render_investigation_report_uses_execution_artifacts_by_default(monkeypatch) -> None:
    monkeypatch.setattr(reporting, "build_investigation_plan", lambda req: _plan())
    monkeypatch.setattr(reporting, "execute_investigation_step", lambda req: _execution())
    monkeypatch.setattr(
        reporting,
        "update_investigation_plan",
        lambda req: req.plan.model_copy(update={"active_batch_id": None}),
    )
    monkeypatch.setattr(
        reporting,
        "rank_hypotheses_from_state",
        lambda _state: InvestigationAnalysis(
            cluster="artifact-cluster",
            scope="service",
            target="service/api-resolved",
            profile="service",
            hypotheses=[
                Hypothesis(
                    key="service-5xx",
                    diagnosis="Artifact analysis",
                    likely_cause="Artifact likely cause",
                    confidence="high",
                    score=1,
                    supporting_findings=[],
                    evidence_items=[],
                )
            ],
            limitations=[],
            recommended_next_step="artifact next step",
            suggested_follow_ups=["artifact follow-up"],
        ),
    )
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    report = reporting.render_investigation_report(
        InvestigationReportRequest(target="service/api", profile="service", include_related_data=False)
    )

    assert report.diagnosis == "Artifact analysis"
    assert report.likely_cause == "Artifact likely cause"
    assert report.recommended_next_step == "artifact next step"
    assert report.tool_path_trace is not None
    assert report.tool_path_trace.planner_path_used is True
    assert report.tool_path_trace.executed_batch_ids == ["batch-1"]
    assert report.tool_path_trace.executed_step_ids == ["collect-target-evidence"]
    assert len(report.tool_path_trace.step_provenance) == 1
    assert report.tool_path_trace.step_provenance[0].step_id == "collect-target-evidence"
    assert report.tool_path_trace.step_provenance[0].provenance.requested_capability == "service_evidence_plane"
    assert report.tool_path_trace.step_provenance[0].provenance.route_satisfaction == "unmatched"
    assert report.tool_path_trace.step_provenance[0].provenance.actual_route.tool_name == "collect_service_evidence"


def test_render_investigation_report_reuses_executed_change_artifacts(monkeypatch) -> None:
    monkeypatch.setattr(reporting, "build_investigation_plan", lambda req: _plan())
    monkeypatch.setattr(reporting, "execute_investigation_step", lambda req: _execution(include_changes=True))
    monkeypatch.setattr(
        reporting,
        "update_investigation_plan",
        lambda req: req.plan.model_copy(update={"active_batch_id": None}),
    )
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))
    monkeypatch.setattr(
        reporting,
        "collect_correlated_changes_for_target",
        lambda target, **kwargs: (_ for _ in ()).throw(AssertionError("executed changes should be reused")),
    )

    report = reporting.render_investigation_report(
        InvestigationReportRequest(target="service/api", profile="service", include_related_data=True)
    )

    assert report.related_data[0].summary == "Deployment rollout"
    assert [trace.step_id for trace in report.tool_path_trace.step_provenance] == [
        "collect-target-evidence",
        "collect-change-candidates",
    ]
    assert report.tool_path_trace.step_provenance[1].provenance.route_satisfaction == "preferred"


def test_render_investigation_report_softens_confidence_when_hypotheses_are_close(monkeypatch) -> None:
    monkeypatch.setattr(reporting, "build_investigation_plan", lambda req: _plan())
    monkeypatch.setattr(reporting, "execute_investigation_step", lambda req: _execution())
    monkeypatch.setattr(
        reporting,
        "update_investigation_plan",
        lambda req: req.plan.model_copy(update={"active_batch_id": None}),
    )
    monkeypatch.setattr(
        reporting,
        "rank_hypotheses_from_state",
        lambda _state: InvestigationAnalysis(
            cluster="artifact-cluster",
            scope="service",
            target="service/api-resolved",
            profile="service",
            hypotheses=[
                Hypothesis(
                    key="service-5xx",
                    diagnosis="Artifact analysis",
                    likely_cause="Artifact likely cause",
                    confidence="high",
                    score=410,
                    supporting_findings=[],
                    evidence_items=[],
                ),
                Hypothesis(
                    key="latency",
                    diagnosis="Latency alternative",
                    likely_cause="Artifact secondary cause",
                    confidence="medium",
                    score=390,
                    supporting_findings=[],
                    evidence_items=[],
                ),
            ],
            limitations=[],
            recommended_next_step="artifact next step",
            suggested_follow_ups=["artifact follow-up"],
        ),
    )
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    report = reporting.render_investigation_report(
        InvestigationReportRequest(target="service/api", profile="service", include_related_data=False)
    )

    assert report.confidence == "medium"
    assert "multiple plausible causes remain" in report.limitations[0]
    assert any(
        "Validate the leading hypothesis against the next most plausible cause" in item
        for item in report.suggested_follow_ups
    )


def test_render_investigation_report_preserves_alert_artifact_evidence(monkeypatch) -> None:
    alert_target = InvestigationTarget(
        source="alert",
        scope="workload",
        cluster="artifact-cluster",
        namespace="artifact-ns",
        requested_target="pod/crashy-abc123",
        target="pod/crashy-abc123",
        profile="workload",
        lookback_minutes=15,
        normalization_notes=["alertname=PodCrashLooping"],
    )
    alert_plan = InvestigationPlan(
        mode="alert_rca",
        objective="Investigate alert PodCrashLooping",
        target=alert_target,
        steps=[],
        evidence_batches=[],
        active_batch_id=None,
        planning_notes=["alertname=PodCrashLooping"],
    )
    monkeypatch.setattr(reporting, "build_investigation_plan", lambda req: alert_plan)
    monkeypatch.setattr(reporting, "execute_investigation_step", lambda req: _alert_execution())
    monkeypatch.setattr(reporting, "update_investigation_plan", lambda req: alert_plan)
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    report = reporting.render_investigation_report(
        InvestigationReportRequest(
            alertname="PodCrashLooping",
            target="pod/crashy",
            labels={"namespace": "artifact-ns", "pod": "crashy-abc123"},
            include_related_data=False,
        )
    )

    assert any("PodCrashLooping" in item for item in report.evidence)
    assert any("requested pod/crashy" in item for item in report.evidence)
    assert any("Resolved runtime target: pod/crashy-abc123" in item for item in report.evidence)
    assert any("crashy-abc123" in item for item in report.evidence)


def test_build_investigation_state_reuses_reconciled_execution_context_without_fallback(monkeypatch) -> None:
    plan = _plan().model_copy(update={"active_batch_id": None})
    execution = _execution()

    monkeypatch.setattr(reporting, "build_investigation_plan", lambda _req: _plan())
    monkeypatch.setattr(
        reporting,
        "execute_investigation_step",
        lambda _req: (_ for _ in ()).throw(AssertionError("fallback execution should not run")),
    )

    state = reporting.build_investigation_state(
        InvestigationReportingRequest(
            target="service/api",
            profile="service",
            execution_context=ReportingExecutionContext(
                updated_plan=plan,
                executions=[execution],
            ),
        )
    )

    assert state.primary_evidence is not None
    assert state.executions == [execution]
    assert state.tool_path_trace is not None
    assert state.tool_path_trace.executed_batch_ids == ["batch-1"]


def test_build_investigation_state_adds_one_bounded_fallback_when_needed(monkeypatch) -> None:
    calls = {"execute": 0}

    monkeypatch.setattr(reporting, "build_investigation_plan", lambda _req: _plan())

    def fake_execute(_req):
        calls["execute"] += 1
        return _execution()

    monkeypatch.setattr(reporting, "execute_investigation_step", fake_execute)
    monkeypatch.setattr(
        reporting,
        "update_investigation_plan",
        lambda req: req.plan.model_copy(update={"active_batch_id": None}),
    )

    state = reporting.build_investigation_state(
        InvestigationReportingRequest(
            target="service/api",
            profile="service",
            execution_context=ReportingExecutionContext(
                updated_plan=_plan(),
                executions=[],
            ),
        )
    )

    assert calls["execute"] == 1
    assert state.primary_evidence is not None
    assert [execution.batch_id for execution in state.executions] == ["batch-1"]


def test_build_investigation_state_respects_disabled_fallback(monkeypatch) -> None:
    monkeypatch.setattr(reporting, "build_investigation_plan", lambda _req: _plan())
    monkeypatch.setattr(
        reporting,
        "execute_investigation_step",
        lambda _req: (_ for _ in ()).throw(AssertionError("fallback execution should stay disabled")),
    )

    state = reporting.build_investigation_state(
        InvestigationReportingRequest(
            target="service/api",
            profile="service",
            execution_context=ReportingExecutionContext(
                updated_plan=_plan(),
                executions=[],
                allow_bounded_fallback_execution=False,
            ),
        )
    )

    assert state.primary_evidence is None
    assert state.executions == []


def test_render_investigation_report_uses_reconciled_execution_context(monkeypatch) -> None:
    monkeypatch.setattr(reporting, "build_investigation_plan", lambda _req: _plan())
    monkeypatch.setattr(
        reporting,
        "execute_investigation_step",
        lambda _req: (_ for _ in ()).throw(AssertionError("fallback execution should not run")),
    )
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    report = reporting.render_investigation_report(
        InvestigationReportingRequest(
            target="service/api",
            profile="service",
            include_related_data=False,
            execution_context=ReportingExecutionContext(
                updated_plan=_plan().model_copy(update={"active_batch_id": None}),
                executions=[_execution()],
            ),
        )
    )

    assert report.diagnosis == "Service instability"
    assert report.tool_path_trace is not None
    assert report.tool_path_trace.executed_step_ids == ["collect-target-evidence"]


def test_advance_investigation_runtime_returns_context_without_fallback(monkeypatch) -> None:
    monkeypatch.setattr(reporting, "build_investigation_plan", lambda _req: _plan())
    monkeypatch.setattr(
        reporting,
        "collect_change_candidates",
        lambda _req: CorrelatedChangesResponse(
            cluster="artifact-cluster",
            scope="service",
            target="service/api-resolved",
            changes=[],
            limitations=[],
        ),
    )
    monkeypatch.setattr(
        reporting,
        "execute_investigation_step",
        lambda _req: (_ for _ in ()).throw(AssertionError("fallback execution should not run")),
    )

    response = reporting.advance_investigation_runtime(
        AdvanceInvestigationRuntimeRequest(
            incident=BuildInvestigationPlanRequest(namespace="artifact-ns", target="service/api", profile="service"),
            execution_context=ReportingExecutionContext(updated_plan=_runtime_plan(), executions=[]),
            submitted_steps=[
                {
                    "step_id": "collect-target-evidence",
                    "evidence_bundle": _bundle(),
                    "actual_route": {
                        "source_kind": "peer_mcp",
                        "mcp_server": "prometheus-mcp-server",
                        "tool_name": "execute_query",
                        "tool_path": ["prometheus-mcp-server", "execute_query"],
                    },
                }
            ],
        )
    )

    assert response.execution_context.allow_bounded_fallback_execution is False
    assert response.execution_context.executions[0].executed_step_ids == [
        "collect-target-evidence",
        "collect-change-candidates",
    ]
    assert response.next_active_batch is None


def test_handoff_active_evidence_batch_prepares_external_batch_without_advancing(monkeypatch) -> None:
    monkeypatch.setattr(reporting, "build_investigation_plan", lambda _req: _plan())
    monkeypatch.setattr(
        reporting,
        "execute_investigation_step",
        lambda _req: (_ for _ in ()).throw(AssertionError("fallback execution should not run")),
    )

    response = reporting.handoff_active_evidence_batch(
        HandoffActiveEvidenceBatchRequest(
            incident=BuildInvestigationPlanRequest(namespace="artifact-ns", target="service/api", profile="service"),
            execution_context=ReportingExecutionContext(updated_plan=_runtime_plan(), executions=[]),
        )
    )

    assert response.execution is None
    assert response.active_batch is not None
    assert response.active_batch.steps[0].step_id == "collect-target-evidence"
    assert response.execution_context.allow_bounded_fallback_execution is False
    assert response.execution_context.executions == []


def test_handoff_active_evidence_batch_advances_after_submission(monkeypatch) -> None:
    monkeypatch.setattr(reporting, "build_investigation_plan", lambda _req: _plan())
    monkeypatch.setattr(
        reporting,
        "collect_change_candidates",
        lambda _req: CorrelatedChangesResponse(
            cluster="artifact-cluster",
            scope="service",
            target="service/api-resolved",
            changes=[],
            limitations=[],
        ),
    )

    response = reporting.handoff_active_evidence_batch(
        HandoffActiveEvidenceBatchRequest(
            incident=BuildInvestigationPlanRequest(namespace="artifact-ns", target="service/api", profile="service"),
            execution_context=ReportingExecutionContext(updated_plan=_runtime_plan(), executions=[]),
            submitted_steps=[
                SubmittedStepArtifact(
                    step_id="collect-target-evidence",
                    evidence_bundle=_bundle(),
                    actual_route=ActualRoute(
                        source_kind="peer_mcp",
                        mcp_server="prometheus-mcp-server",
                        tool_name="execute_query",
                        tool_path=["prometheus-mcp-server", "execute_query"],
                    ),
                )
            ],
        )
    )

    assert response.execution is not None
    assert response.execution.executed_step_ids == [
        "collect-target-evidence",
        "collect-change-candidates",
    ]
    assert response.active_batch is None
    assert response.execution_context.allow_bounded_fallback_execution is False


def test_handoff_active_evidence_batch_auto_advances_planner_owned_batch(monkeypatch) -> None:
    monkeypatch.setattr(reporting, "build_investigation_plan", lambda _req: _plan())
    monkeypatch.setattr(
        reporting,
        "_planner_deps",
        lambda: reporting.PlannerDeps(
            normalize_alert_input=lambda req: (_ for _ in ()).throw(AssertionError("unexpected alert normalization")),
            canonical_target=lambda target, profile, service_name: target,
            scope_from_target=lambda target, profile: "workload",
            resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": "artifact-cluster"})(),
            get_backend_cr=lambda *args, **kwargs: {},
            get_frontend_cr=lambda *args, **kwargs: {},
            get_cluster_cr=lambda *args, **kwargs: {},
            find_unhealthy_pod=lambda req: None,
            collect_alert_evidence=lambda req: _bundle().model_copy(
                update={
                    "target": TargetRef(namespace="artifact-ns", kind="pod", name="api-alert"),
                    "object_state": {"kind": "pod", "name": "api-alert"},
                }
            ),
            collect_node_evidence=lambda req: _bundle().model_copy(
                update={
                    "target": TargetRef(namespace="artifact-ns", kind="node", name=req.node_name),
                    "object_state": {"kind": "node", "name": req.node_name},
                }
            ),
            collect_service_evidence=lambda req: _bundle().model_copy(
                update={
                    "target": TargetRef(namespace="artifact-ns", kind="service", name=req.service_name),
                    "object_state": {"kind": "service", "name": req.service_name},
                }
            ),
            collect_workload_evidence=lambda req: _bundle().model_copy(
                update={
                    "target": TargetRef(namespace="artifact-ns", kind="pod", name="api"),
                    "object_state": {"kind": "pod", "name": "api"},
                }
            ),
            collect_change_candidates=lambda req: CorrelatedChangesResponse(
                cluster="artifact-cluster",
                scope="workload",
                target="pod/api",
                changes=[],
                limitations=[],
            ),
        ),
    )
    alert_target = InvestigationTarget(
        source="alert",
        scope="workload",
        cluster="artifact-cluster",
        namespace="artifact-ns",
        requested_target="pod/api",
        target="pod/api",
        node_name=None,
        service_name=None,
        profile="workload",
        lookback_minutes=15,
        normalization_notes=["alertname=PodCrashLooping"],
    )
    alert_plan = InvestigationPlan(
        mode="alert_rca",
        objective="Investigate alert PodCrashLooping",
        target=alert_target,
        steps=[
            PlanStep(
                id="collect-alert-evidence",
                title="Collect alert evidence",
                category="evidence",
                plane="alert",
                rationale="Collect alert evidence",
                suggested_capability="alert_evidence_plane",
            )
        ],
        evidence_batches=[
            EvidenceBatch(
                id="batch-1",
                title="Initial evidence",
                status="pending",
                intent="Collect alert evidence.",
                step_ids=["collect-alert-evidence"],
            )
        ],
        active_batch_id="batch-1",
        planning_notes=["alertname=PodCrashLooping"],
    )

    response = reporting.handoff_active_evidence_batch(
        HandoffActiveEvidenceBatchRequest(
            incident=BuildInvestigationPlanRequest(
                namespace="artifact-ns",
                target="pod/api",
                alertname="PodCrashLooping",
            ),
            execution_context=ReportingExecutionContext(updated_plan=alert_plan, executions=[]),
        )
    )

    assert response.execution is not None
    assert response.execution.executed_step_ids == ["collect-alert-evidence"]
    assert response.active_batch is None


def test_render_investigation_report_uses_advanced_runtime_context_without_fallback(monkeypatch) -> None:
    monkeypatch.setattr(reporting, "build_investigation_plan", lambda _req: _plan())
    monkeypatch.setattr(
        reporting,
        "collect_change_candidates",
        lambda _req: CorrelatedChangesResponse(
            cluster="artifact-cluster",
            scope="service",
            target="service/api-resolved",
            changes=[],
            limitations=[],
        ),
    )
    monkeypatch.setattr(
        reporting,
        "execute_investigation_step",
        lambda _req: (_ for _ in ()).throw(AssertionError("fallback execution should remain disabled")),
    )
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: ([], []))

    runtime = reporting.advance_investigation_runtime(
        AdvanceInvestigationRuntimeRequest(
            incident=BuildInvestigationPlanRequest(namespace="artifact-ns", target="service/api", profile="service"),
            execution_context=ReportingExecutionContext(updated_plan=_runtime_plan(), executions=[]),
            submitted_steps=[
                SubmittedStepArtifact(
                    step_id="collect-target-evidence",
                    evidence_bundle=_bundle(),
                    actual_route=ActualRoute(
                        source_kind="peer_mcp",
                        mcp_server="prometheus-mcp-server",
                        tool_name="execute_query",
                        tool_path=["prometheus-mcp-server", "execute_query"],
                    ),
                )
            ],
        )
    )

    report = reporting.render_investigation_report(
        InvestigationReportingRequest(
            target="service/api",
            profile="service",
            include_related_data=False,
            execution_context=runtime.execution_context,
        )
    )

    assert report.diagnosis == "Service instability"
    assert report.tool_path_trace is not None
    assert report.tool_path_trace.executed_step_ids == [
        "collect-target-evidence",
        "collect-change-candidates",
    ]
