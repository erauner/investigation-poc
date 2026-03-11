import pytest

from investigation_service.models import (
    ActualRoute,
    BuildInvestigationPlanRequest,
    GetActiveEvidenceBatchRequest,
    CorrelatedChangesResponse,
    EvidenceBatch,
    EvidenceBatchExecution,
    EvidenceBundle,
    ExecuteInvestigationStepRequest,
    Finding,
    InvestigationPlan,
    InvestigationReportRequest,
    NormalizedInvestigationRequest,
    PlanStep,
    SubmitEvidenceArtifactsRequest,
    StepRouteProvenance,
    TargetRef,
    SubmittedStepArtifact,
    UpdateInvestigationPlanRequest,
)
from investigation_service.planner import (
    advance_active_evidence_batch,
    PlannerDeps,
    build_investigation_plan,
    classify_investigation_mode,
    execute_investigation_step,
    get_active_evidence_batch_contract,
    resolve_primary_target,
    submit_evidence_step_artifacts,
    update_investigation_plan,
)


def _bundle(
    *,
    kind: str = "pod",
    name: str = "api",
    findings: list[Finding] | None = None,
    limitations: list[str] | None = None,
) -> EvidenceBundle:
    return EvidenceBundle(
        cluster="erauner-home",
        target=TargetRef(namespace="default", kind=kind, name=name),
        object_state={"kind": kind, "name": name},
        events=[],
        log_excerpt="",
        metrics={},
        findings=findings or [],
        limitations=limitations or [],
        enrichment_hints=[],
    )


def _changes(limitations: list[str] | None = None) -> CorrelatedChangesResponse:
    return CorrelatedChangesResponse(
        cluster="erauner-home",
        scope="workload",
        target="deployment/api",
        changes=[],
        limitations=limitations or [],
    )


def _deps(calls: list[str] | None = None) -> PlannerDeps:
    calls = calls if calls is not None else []
    return PlannerDeps(
        normalize_alert_input=lambda req: (_ for _ in ()).throw(AssertionError(f"unexpected alert normalization: {req}")),
        canonical_target=lambda target, profile, service_name: calls.append("canonical_target") or target,
        scope_from_target=lambda target, profile: calls.append("scope_from_target") or "workload",
        resolve_cluster=lambda cluster: calls.append("resolve_cluster")
        or type("ResolvedCluster", (), {"alias": "erauner-home"})(),
        get_backend_cr=lambda *args, **kwargs: calls.append("get_backend_cr") or {"metadata": {"name": "api"}},
        get_frontend_cr=lambda *args, **kwargs: calls.append("get_frontend_cr") or {},
        get_cluster_cr=lambda *args, **kwargs: calls.append("get_cluster_cr") or {},
        find_unhealthy_pod=lambda req: calls.append("find_unhealthy_pod") or None,
        collect_alert_evidence=lambda req: calls.append("collect_alert_evidence")
        or _bundle(kind="pod", name="api-alert", findings=[Finding(severity="warning", source="events", title="Alert fired", evidence="alert evidence")]),
        collect_node_evidence=lambda req: calls.append("collect_node_evidence") or _bundle(kind="node", name=req.node_name),
        collect_service_evidence=lambda req: calls.append("collect_service_evidence")
        or _bundle(kind="service", name=req.service_name),
        collect_workload_evidence=lambda req: calls.append("collect_workload_evidence")
        or _bundle(kind="pod", name=req.target.split("/", 1)[1]),
        collect_change_candidates=lambda req: calls.append("collect_change_candidates") or _changes(),
    )


def test_classify_investigation_mode_detects_alert_requests() -> None:
    mode = classify_investigation_mode(
        BuildInvestigationPlanRequest(
            alertname="PodCrashLooping",
            labels={"namespace": "default", "pod": "api-123"},
        )
    )

    assert mode == "alert_rca"


def test_classify_investigation_mode_detects_factual_questions() -> None:
    mode = classify_investigation_mode(
        BuildInvestigationPlanRequest(
            objective="factual",
            question="What is consuming the most memory in the cluster?",
        )
    )

    assert mode == "factual_analysis"


def test_classify_investigation_mode_defaults_to_targeted_rca() -> None:
    mode = classify_investigation_mode(BuildInvestigationPlanRequest(target="pod/api"))

    assert mode == "targeted_rca"


def test_classify_investigation_mode_promotes_resolved_question_targets_to_targeted_rca() -> None:
    mode = classify_investigation_mode(
        BuildInvestigationPlanRequest(question="Investigate pod/api in namespace default"),
        has_resolved_target=True,
    )

    assert mode == "targeted_rca"


def test_build_investigation_plan_creates_targeted_plan_without_collecting_evidence() -> None:
    calls: list[str] = []

    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="pod/api", question="Investigate api"),
        _deps(calls),
    )

    assert plan.mode == "targeted_rca"
    assert plan.target is not None
    assert plan.target.target == "pod/api"
    assert plan.active_batch_id == "batch-1"
    assert [step.id for step in plan.steps] == [
        "collect-target-evidence",
        "collect-change-candidates",
        "rank-hypotheses",
        "render-report",
    ]
    target_step = next(step for step in plan.steps if step.id == "collect-target-evidence")
    assert target_step.suggested_capability == "workload_evidence_plane"
    assert target_step.preferred_mcp_server == "kubernetes-mcp-server"
    assert "pods_log" in target_step.preferred_tool_names
    assert [batch.id for batch in plan.evidence_batches] == ["batch-1", "batch-2", "batch-3"]
    assert calls == []


def test_build_investigation_plan_sets_metrics_first_policy_for_service_targets() -> None:
    calls: list[str] = []
    deps = _deps(calls)
    deps = PlannerDeps(
        **{
            **deps.__dict__,
            "scope_from_target": lambda target, profile: calls.append("scope_from_target") or "service",
        }
    )

    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(
            namespace="default",
            target="service/api",
            profile="service",
            service_name="api",
        ),
        deps,
    )

    target_step = next(step for step in plan.steps if step.id == "collect-target-evidence")
    assert target_step.suggested_capability == "service_evidence_plane"
    assert target_step.preferred_mcp_server == "prometheus-mcp-server"
    assert target_step.preferred_tool_names == ["execute_query", "execute_range_query"]
    assert target_step.fallback_mcp_server == "kubernetes-mcp-server"
    assert target_step.fallback_tool_names == ["resources_get", "events_list", "pods_list_in_namespace"]


def test_build_investigation_plan_keeps_alert_step_internal_only_in_public_metadata() -> None:
    deps = _deps([])
    deps = PlannerDeps(
        **{
            **deps.__dict__,
            "normalize_alert_input": lambda req: NormalizedInvestigationRequest(
                source="alert",
                scope="workload",
                cluster="erauner-home",
                namespace=req.namespace,
                target=req.target or "pod/api",
                profile=req.profile,
                lookback_minutes=req.lookback_minutes,
                normalization_notes=["alertname=PodCrashLooping"],
            ),
        }
    )
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(
            alertname="PodCrashLooping",
            namespace="default",
            target="pod/api",
        ),
        deps,
    )

    alert_step = next(step for step in plan.steps if step.id == "collect-alert-evidence")

    assert alert_step.suggested_capability == "alert_evidence_plane"
    assert alert_step.preferred_mcp_server is None
    assert alert_step.preferred_tool_names == []
    assert alert_step.fallback_mcp_server is None
    assert alert_step.fallback_tool_names == []


def test_build_investigation_plan_resolves_convenience_targets_before_plan_construction() -> None:
    calls: list[str] = []
    deps = _deps(calls)
    deps = PlannerDeps(
        **{
            **deps.__dict__,
            "canonical_target": lambda target, profile, service_name: calls.append("canonical_target") or "Backend/api",
            "scope_from_target": lambda target, profile: calls.append("scope_from_target") or "workload",
        }
    )

    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="Backend/api"),
        deps,
    )

    assert plan.target is not None
    assert plan.target.requested_target == "Backend/api"
    assert plan.target.target == "deployment/api"
    assert "resolved Backend/api to deployment/api" in plan.planning_notes
    assert calls == ["resolve_cluster", "get_backend_cr"]


def test_build_investigation_plan_supports_factual_mode_without_a_target() -> None:
    calls: list[str] = []

    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(
            objective="factual",
            question="What are the biggest resource consumers in the cluster?",
        ),
        _deps(calls),
    )

    assert plan.mode == "factual_analysis"
    assert plan.target is None
    assert plan.active_batch_id == "batch-1"
    assert [step.id for step in plan.steps] == ["collect-factual-evidence", "summarize-findings"]
    assert calls == []


def test_build_investigation_plan_uses_question_ingress_to_resolve_target() -> None:
    calls: list[str] = []

    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(
            namespace="default",
            question="Investigate pod/api in namespace default",
        ),
        _deps(calls),
    )

    assert plan.mode == "targeted_rca"
    assert plan.target is not None
    assert plan.target.requested_target == "pod/api"
    assert plan.target.target == "pod/api"
    assert "canonical focus selected: pod/api" in plan.planning_notes
    assert calls == []


def test_build_investigation_plan_reraises_question_scope_errors_for_target_like_input() -> None:
    deps = _deps([])
    deps = PlannerDeps(
        **{
            **deps.__dict__,
            "resolve_cluster": lambda cluster: (_ for _ in ()).throw(ValueError(f"unknown cluster alias: {cluster}")),
        }
    )

    with pytest.raises(ValueError, match="unknown cluster alias: typoed-cluster"):
        build_investigation_plan(
            BuildInvestigationPlanRequest(
                question="Investigate pod/api in namespace default in cluster typoed-cluster",
            ),
            deps,
        )


def test_resolve_primary_target_normalizes_question_cluster_text_via_cluster_registry() -> None:
    deps = _deps([])
    deps = PlannerDeps(
        **{
            **deps.__dict__,
            "resolve_cluster": lambda cluster: type(
                "ResolvedCluster",
                (),
                {"alias": "current-context", "source": "legacy_current_context"},
            )(),
        }
    )

    target = resolve_primary_target(
        InvestigationReportRequest(
            namespace="default",
            question="Investigate pod/api in namespace default in cluster current-context",
        ),
        deps,
    )

    assert target.cluster is None
    assert target.target == "pod/api"


def test_execute_investigation_step_runs_single_targeted_evidence_batch() -> None:
    calls: list[str] = []
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        _deps(calls),
    )

    execution = execute_investigation_step(
        ExecuteInvestigationStepRequest(
            plan=plan,
            incident=BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        ),
        _deps(calls),
    )

    assert execution.batch_id == "batch-1"
    assert execution.executed_step_ids == ["collect-target-evidence", "collect-change-candidates"]
    assert execution.artifacts[0].artifact_type == "evidence_bundle"
    assert execution.artifacts[1].artifact_type == "change_candidates"
    assert execution.artifacts[0].route_provenance is not None
    assert execution.artifacts[0].route_provenance.requested_capability == "workload_evidence_plane"
    assert execution.artifacts[0].route_provenance.route_satisfaction == "unmatched"
    assert execution.artifacts[0].route_provenance.actual_route.mcp_server == "investigation-mcp-server"
    assert execution.artifacts[0].route_provenance.actual_route.tool_name == "collect_workload_evidence"
    assert execution.artifacts[0].route_provenance.actual_route.tool_path == [
        "planner._execute_step",
        "deps.collect_workload_evidence",
    ]
    assert execution.artifacts[1].route_provenance is not None
    assert execution.artifacts[1].route_provenance.requested_capability == "collect_change_candidates"
    assert execution.artifacts[1].route_provenance.route_satisfaction == "preferred"
    assert execution.artifacts[1].route_provenance.actual_route.tool_name == "collect_change_candidates"
    assert calls[-2:] == ["collect_workload_evidence", "collect_change_candidates"]


def test_execute_investigation_step_runs_alert_batch_from_alert_input() -> None:
    calls: list[str] = []
    deps = _deps(calls)
    deps = PlannerDeps(
        **{
            **deps.__dict__,
            "normalize_alert_input": lambda req: NormalizedInvestigationRequest(
                source="alert",
                scope="workload",
                cluster=req.cluster,
                namespace=req.labels.get("namespace"),
                target=f"pod/{req.labels['pod']}",
                node_name=None,
                service_name=None,
                profile="workload",
                lookback_minutes=req.lookback_minutes,
                normalization_notes=["alert normalized"],
            ),
        }
    )
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(
            alertname="PodCrashLooping",
            labels={"namespace": "default", "pod": "api-123"},
        ),
        deps,
    )

    execution = execute_investigation_step(
        ExecuteInvestigationStepRequest(
            plan=plan,
            incident=BuildInvestigationPlanRequest(
                alertname="PodCrashLooping",
                labels={"namespace": "default", "pod": "api-123"},
            ),
        ),
        deps,
    )

    assert execution.executed_step_ids == [
        "collect-alert-evidence",
        "collect-target-evidence",
        "collect-change-candidates",
    ]
    assert execution.artifacts[0].route_provenance is not None
    assert execution.artifacts[0].route_provenance.requested_capability == "alert_evidence_plane"
    assert execution.artifacts[0].route_provenance.route_satisfaction == "not_applicable"
    assert execution.artifacts[0].route_provenance.actual_route.tool_name == "collect_alert_evidence"
    assert execution.artifacts[1].route_provenance is not None
    assert execution.artifacts[1].route_provenance.requested_capability == "workload_evidence_plane"
    assert execution.artifacts[1].route_provenance.route_satisfaction == "unmatched"
    assert "collect_alert_evidence" in calls


def test_execute_investigation_step_keeps_internal_service_follow_up_unmatched() -> None:
    calls: list[str] = []
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(
            namespace="default",
            target="service/api",
            profile="service",
            service_name="api",
        ),
        _deps(calls),
    ).model_copy(
        update={
            "steps": [
                PlanStep(
                    id="collect-service-follow-up-evidence",
                    title="Collect service follow-up evidence",
                    category="evidence",
                    plane="service",
                    status="pending",
                    rationale="Follow up with service evidence.",
                    suggested_capability="service_evidence_plane",
                    preferred_mcp_server="prometheus-mcp-server",
                    preferred_tool_names=["execute_query", "execute_range_query"],
                    fallback_mcp_server="kubernetes-mcp-server",
                    fallback_tool_names=["collect_service_evidence"],
                    depends_on=[],
                )
            ],
            "evidence_batches": [
                EvidenceBatch(
                    id="batch-follow-up-service",
                    title="Service follow-up",
                    status="pending",
                    intent="Collect follow-up evidence",
                    step_ids=["collect-service-follow-up-evidence"],
                )
            ],
            "active_batch_id": "batch-follow-up-service",
        }
    )

    execution = execute_investigation_step(
        ExecuteInvestigationStepRequest(
            plan=plan,
            incident=BuildInvestigationPlanRequest(
                namespace="default",
                target="service/api",
                profile="service",
                service_name="api",
            ),
        ),
        _deps(calls),
    )

    assert execution.executed_step_ids == ["collect-service-follow-up-evidence"]
    assert execution.artifacts[0].route_provenance is not None
    assert execution.artifacts[0].route_provenance.requested_capability == "service_evidence_plane"
    assert execution.artifacts[0].route_provenance.route_satisfaction == "unmatched"
    assert execution.artifacts[0].route_provenance.actual_route.tool_name == "collect_service_evidence"


def test_get_active_evidence_batch_contract_exposes_execution_inputs() -> None:
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        _deps([]),
    )

    contract = get_active_evidence_batch_contract(
        GetActiveEvidenceBatchRequest(
            plan=plan,
            incident=BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        )
    )

    assert contract.batch_id == "batch-1"
    assert contract.subject.kind == "target"
    assert contract.canonical_target is not None
    assert [step.step_id for step in contract.steps] == [
        "collect-target-evidence",
        "collect-change-candidates",
    ]
    assert contract.steps[0].execution_mode == "external_preferred"
    assert contract.steps[0].execution_inputs.request_kind == "target_context"
    assert contract.steps[0].execution_inputs.target == "deployment/api"
    assert contract.steps[1].execution_mode == "control_plane_only"
    assert contract.steps[1].execution_inputs.request_kind == "change_candidates"


def test_get_active_evidence_batch_contract_uses_service_context_for_service_targets() -> None:
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="service/api", profile="service", service_name="api"),
        _deps([]),
    )

    contract = get_active_evidence_batch_contract(
        GetActiveEvidenceBatchRequest(
            plan=plan,
            incident=BuildInvestigationPlanRequest(
                namespace="default",
                target="service/api",
                profile="service",
                service_name="api",
            ),
        )
    )

    assert contract.steps[0].step_id == "collect-target-evidence"
    assert contract.steps[0].execution_inputs.request_kind == "service_context"
    assert contract.steps[0].execution_inputs.target == "service/api"
    assert contract.steps[0].execution_inputs.service_name == "api"


def test_submit_evidence_step_artifacts_reconciles_partial_batch_without_completing_it() -> None:
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        _deps([]),
    )

    result = submit_evidence_step_artifacts(
        SubmitEvidenceArtifactsRequest(
            plan=plan,
            incident=BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
            submitted_steps=[
                SubmittedStepArtifact(
                    step_id="collect-target-evidence",
                    evidence_bundle=_bundle(),
                    actual_route=ActualRoute(
                        source_kind="peer_mcp",
                        mcp_server="kubernetes-mcp-server",
                        tool_name="resources_get",
                        tool_path=["kubernetes-mcp-server", "resources_get"],
                    ),
                )
            ],
        )
    )

    assert result.execution.batch_id == "batch-1"
    assert result.execution.executed_step_ids == ["collect-target-evidence"]
    assert result.execution.artifacts[0].route_provenance is not None
    assert result.execution.artifacts[0].route_provenance.route_satisfaction == "preferred"
    updated = result.updated_plan
    target_step = next(step for step in updated.steps if step.id == "collect-target-evidence")
    change_step = next(step for step in updated.steps if step.id == "collect-change-candidates")
    batch = next(batch for batch in updated.evidence_batches if batch.id == "batch-1")
    assert target_step.status == "completed"
    assert change_step.status == "pending"
    assert batch.status == "pending"
    assert updated.active_batch_id == "batch-1"


def test_get_active_evidence_batch_contract_only_returns_pending_steps_after_partial_submission() -> None:
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        _deps([]),
    )
    result = submit_evidence_step_artifacts(
        SubmitEvidenceArtifactsRequest(
            plan=plan,
            incident=BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
            submitted_steps=[
                SubmittedStepArtifact(
                    step_id="collect-target-evidence",
                    evidence_bundle=_bundle(),
                    actual_route=ActualRoute(
                        source_kind="peer_mcp",
                        mcp_server="kubernetes-mcp-server",
                        tool_name="resources_get",
                        tool_path=["kubernetes-mcp-server", "resources_get"],
                    ),
                )
            ],
        )
    )

    contract = get_active_evidence_batch_contract(
        GetActiveEvidenceBatchRequest(
            plan=result.updated_plan,
            incident=BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        )
    )

    assert contract.batch_id == "batch-1"
    assert [step.step_id for step in contract.steps] == ["collect-change-candidates"]
    assert contract.steps[0].execution_mode == "control_plane_only"


def test_execute_investigation_step_only_runs_remaining_pending_steps_after_partial_submission() -> None:
    calls: list[str] = []
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        _deps(calls),
    )
    result = submit_evidence_step_artifacts(
        SubmitEvidenceArtifactsRequest(
            plan=plan,
            incident=BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
            submitted_steps=[
                SubmittedStepArtifact(
                    step_id="collect-target-evidence",
                    evidence_bundle=_bundle(),
                    actual_route=ActualRoute(
                        source_kind="peer_mcp",
                        mcp_server="kubernetes-mcp-server",
                        tool_name="resources_get",
                        tool_path=["kubernetes-mcp-server", "resources_get"],
                    ),
                )
            ],
        )
    )

    execution = execute_investigation_step(
        ExecuteInvestigationStepRequest(
            plan=result.updated_plan,
            incident=BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        ),
        _deps(calls),
    )

    assert execution.executed_step_ids == ["collect-change-candidates"]
    assert execution.artifacts[0].step_id == "collect-change-candidates"
    assert calls[-1] == "collect_change_candidates"


def test_advance_active_evidence_batch_combines_submitted_and_control_plane_steps() -> None:
    calls: list[str] = []
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        _deps(calls),
    )

    result = advance_active_evidence_batch(
        plan=plan,
        incident=BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        submitted_steps=[
            SubmittedStepArtifact(
                step_id="collect-target-evidence",
                evidence_bundle=_bundle(),
                actual_route=ActualRoute(
                    source_kind="peer_mcp",
                    mcp_server="kubernetes-mcp-server",
                    tool_name="resources_get",
                    tool_path=["kubernetes-mcp-server", "resources_get"],
                ),
            )
        ],
        batch_id=None,
        deps=_deps(calls),
    )

    assert result.execution.executed_step_ids == ["collect-target-evidence", "collect-change-candidates"]
    assert result.execution.artifacts[0].route_provenance is not None
    assert result.execution.artifacts[0].route_provenance.route_satisfaction == "preferred"
    assert result.execution.artifacts[1].step_id == "collect-change-candidates"
    assert result.updated_plan.active_batch_id is None
    assert calls[-1] == "collect_change_candidates"
    assert "collect_workload_evidence" not in calls


def test_advance_active_evidence_batch_rejects_missing_external_submission() -> None:
    deps = _deps([])
    deps = PlannerDeps(
        **{
            **deps.__dict__,
            "scope_from_target": lambda target, profile: "service",
        }
    )
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="service/api", profile="service", service_name="api"),
        deps,
    )

    with pytest.raises(ValueError, match="active batch still requires external evidence submission for: collect-target-evidence"):
        advance_active_evidence_batch(
            plan=plan,
            incident=BuildInvestigationPlanRequest(namespace="default", target="service/api", profile="service", service_name="api"),
            submitted_steps=[],
            batch_id=None,
            deps=deps,
        )


def test_advance_active_evidence_batch_still_rejects_workload_batch_without_attempt_metadata() -> None:
    deps = _deps([])
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        deps,
    )

    with pytest.raises(ValueError, match="active batch still requires external evidence submission for: collect-target-evidence"):
        advance_active_evidence_batch(
            plan=plan,
            incident=BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
            submitted_steps=[],
            batch_id=None,
            deps=deps,
        )


def test_advance_active_evidence_batch_keeps_alert_evidence_planner_owned() -> None:
    calls: list[str] = []
    deps = _deps(calls)
    deps = PlannerDeps(
        **{
            **deps.__dict__,
            "normalize_alert_input": lambda req: NormalizedInvestigationRequest(
                source="alert",
                scope="workload",
                cluster=req.cluster,
                namespace=req.labels.get("namespace"),
                target=f"pod/{req.labels['pod']}",
                node_name=None,
                service_name=None,
                profile="workload",
                lookback_minutes=req.lookback_minutes,
                normalization_notes=["alert normalized"],
            ),
        }
    )
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(
            alertname="PodCrashLooping",
            labels={"namespace": "default", "pod": "api-123"},
        ),
        deps,
    )

    result = advance_active_evidence_batch(
        plan=plan,
        incident=BuildInvestigationPlanRequest(
            alertname="PodCrashLooping",
            labels={"namespace": "default", "pod": "api-123"},
        ),
        submitted_steps=[
            SubmittedStepArtifact(
                step_id="collect-target-evidence",
                evidence_bundle=_bundle(name="api-123"),
                actual_route=ActualRoute(
                    source_kind="peer_mcp",
                    mcp_server="kubernetes-mcp-server",
                    tool_name="resources_get",
                    tool_path=["kubernetes-mcp-server", "resources_get"],
                ),
            )
        ],
        batch_id=None,
        deps=deps,
    )

    assert result.execution.executed_step_ids == [
        "collect-alert-evidence",
        "collect-target-evidence",
        "collect-change-candidates",
    ]
    assert "collect_alert_evidence" in calls
    assert "collect_change_candidates" in calls
    assert "collect_workload_evidence" not in calls


def test_submit_evidence_step_artifacts_rejects_control_plane_only_steps() -> None:
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        _deps([]),
    )

    with pytest.raises(ValueError, match="control-plane-only"):
        submit_evidence_step_artifacts(
            SubmitEvidenceArtifactsRequest(
                plan=plan,
                incident=BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
                submitted_steps=[
                    SubmittedStepArtifact(
                        step_id="collect-change-candidates",
                        change_candidates=_changes(),
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


def test_advance_active_evidence_batch_preserves_service_follow_up_insertion() -> None:
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="deployment/api", service_name="api"),
        _deps([]),
    )

    result = advance_active_evidence_batch(
        plan=plan,
        incident=BuildInvestigationPlanRequest(namespace="default", target="deployment/api", service_name="api"),
        submitted_steps=[
            SubmittedStepArtifact(
                step_id="collect-target-evidence",
                evidence_bundle=_bundle(
                    findings=[
                        Finding(
                            severity="info",
                            source="heuristic",
                            title="No Critical Signals Found",
                            evidence="nothing decisive",
                        )
                    ]
                ),
                actual_route=ActualRoute(
                    source_kind="peer_mcp",
                    mcp_server="kubernetes-mcp-server",
                    tool_name="resources_get",
                    tool_path=["kubernetes-mcp-server", "resources_get"],
                ),
            )
        ],
        batch_id=None,
        deps=_deps([]),
    )

    assert result.updated_plan.active_batch_id == "batch-follow-up-service"
    follow_up = next(step for step in result.updated_plan.steps if step.id == "collect-service-follow-up-evidence")
    assert follow_up.status == "pending"


def test_advance_active_evidence_batch_preserves_workload_peer_failure_provenance() -> None:
    calls: list[str] = []
    deps = _deps(calls)
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        deps,
    )

    result = advance_active_evidence_batch(
        plan=plan,
        incident=BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        submitted_steps=[
            SubmittedStepArtifact(
                step_id="collect-target-evidence",
                actual_route=ActualRoute(
                    source_kind="peer_mcp",
                    mcp_server="kubernetes-mcp-server",
                    tool_name="resources_get",
                    tool_path=["kubernetes-mcp-server", "resources_get", "events_list", "pods_log"],
                ),
                limitations=["peer workload MCP attempt failed: peer unavailable"],
            )
        ],
        batch_id="batch-1",
        deps=deps,
    )

    artifact = next(item for item in result.execution.artifacts if item.step_id == "collect-target-evidence")
    assert artifact.route_provenance == StepRouteProvenance(
        requested_capability="workload_evidence_plane",
        route_satisfaction="unmatched",
        actual_route=ActualRoute(
            source_kind="investigation_internal",
            mcp_server="investigation-mcp-server",
            tool_name="collect_workload_evidence",
            tool_path=["planner._execute_step", "deps.collect_workload_evidence"],
        ),
        attempted_routes=[
            ActualRoute(
                source_kind="peer_mcp",
                mcp_server="kubernetes-mcp-server",
                tool_name="resources_get",
                tool_path=["kubernetes-mcp-server", "resources_get", "events_list", "pods_log"],
            )
        ],
    )
    assert "peer workload MCP attempt failed: peer unavailable" in artifact.limitations
    assert "peer workload MCP attempt failed: peer unavailable" in result.execution.execution_notes


def test_advance_active_evidence_batch_preserves_service_peer_failure_provenance() -> None:
    calls: list[str] = []
    deps = _deps(calls)
    deps = PlannerDeps(
        **{
            **deps.__dict__,
            "scope_from_target": lambda target, profile: "service",
        }
    )
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="service/api", profile="service", service_name="api"),
        deps,
    )

    result = advance_active_evidence_batch(
        plan=plan,
        incident=BuildInvestigationPlanRequest(
            namespace="default",
            target="service/api",
            profile="service",
            service_name="api",
        ),
        submitted_steps=[
            SubmittedStepArtifact(
                step_id="collect-target-evidence",
                actual_route=ActualRoute(
                    source_kind="peer_mcp",
                    mcp_server="prometheus-mcp-server",
                    tool_name=None,
                    tool_path=["prometheus-mcp-server"],
                ),
                attempted_routes=[
                    ActualRoute(
                        source_kind="peer_mcp",
                        mcp_server="prometheus-mcp-server",
                        tool_name=None,
                        tool_path=["prometheus-mcp-server"],
                    ),
                    ActualRoute(
                        source_kind="peer_mcp",
                        mcp_server="kubernetes-mcp-server",
                        tool_name=None,
                        tool_path=["kubernetes-mcp-server"],
                    ),
                ],
                limitations=["prometheus peer failed: prom down", "kubernetes peer fallback failed: kube down"],
            )
        ],
        batch_id="batch-1",
        deps=deps,
    )

    artifact = next(item for item in result.execution.artifacts if item.step_id == "collect-target-evidence")
    assert artifact.route_provenance == StepRouteProvenance(
        requested_capability="service_evidence_plane",
        route_satisfaction="unmatched",
        actual_route=ActualRoute(
            source_kind="investigation_internal",
            mcp_server="investigation-mcp-server",
            tool_name="collect_service_evidence",
            tool_path=["planner._execute_step", "deps.collect_service_evidence"],
        ),
        attempted_routes=[
            ActualRoute(
                source_kind="peer_mcp",
                mcp_server="prometheus-mcp-server",
                tool_name=None,
                tool_path=["prometheus-mcp-server"],
            ),
            ActualRoute(
                source_kind="peer_mcp",
                mcp_server="kubernetes-mcp-server",
                tool_name=None,
                tool_path=["kubernetes-mcp-server"],
            ),
        ],
    )
    assert "prometheus peer failed: prom down" in artifact.limitations
    assert "kubernetes peer fallback failed: kube down" in artifact.limitations
    assert "prometheus peer failed: prom down" in result.execution.execution_notes
    assert "kubernetes peer fallback failed: kube down" in result.execution.execution_notes


def test_advance_active_evidence_batch_preserves_node_peer_failure_provenance() -> None:
    calls: list[str] = []
    deps = _deps(calls)
    deps = PlannerDeps(
        **{
            **deps.__dict__,
            "scope_from_target": lambda target, profile: "node",
        }
    )
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(target="node/worker3", profile="workload", node_name="worker3"),
        deps,
    )

    result = advance_active_evidence_batch(
        plan=plan,
        incident=BuildInvestigationPlanRequest(target="node/worker3", profile="workload", node_name="worker3"),
        submitted_steps=[
            SubmittedStepArtifact(
                step_id="collect-target-evidence",
                actual_route=ActualRoute(
                    source_kind="peer_mcp",
                    mcp_server="prometheus-mcp-server",
                    tool_name=None,
                    tool_path=["prometheus-mcp-server"],
                ),
                attempted_routes=[
                    ActualRoute(
                        source_kind="peer_mcp",
                        mcp_server="prometheus-mcp-server",
                        tool_name=None,
                        tool_path=["prometheus-mcp-server"],
                    ),
                    ActualRoute(
                        source_kind="peer_mcp",
                        mcp_server="kubernetes-mcp-server",
                        tool_name=None,
                        tool_path=["kubernetes-mcp-server"],
                    ),
                ],
                limitations=["prometheus peer failed: prom down", "kubernetes peer fallback failed: kube down"],
            )
        ],
        batch_id="batch-1",
        deps=deps,
    )

    artifact = next(item for item in result.execution.artifacts if item.step_id == "collect-target-evidence")
    assert artifact.route_provenance == StepRouteProvenance(
        requested_capability="node_evidence_plane",
        route_satisfaction="unmatched",
        actual_route=ActualRoute(
            source_kind="investigation_internal",
            mcp_server="investigation-mcp-server",
            tool_name="collect_node_evidence",
            tool_path=["planner._execute_step", "deps.collect_node_evidence"],
        ),
        attempted_routes=[
            ActualRoute(
                source_kind="peer_mcp",
                mcp_server="prometheus-mcp-server",
                tool_name=None,
                tool_path=["prometheus-mcp-server"],
            ),
            ActualRoute(
                source_kind="peer_mcp",
                mcp_server="kubernetes-mcp-server",
                tool_name=None,
                tool_path=["kubernetes-mcp-server"],
            ),
        ],
    )
    assert "prometheus peer failed: prom down" in artifact.limitations
    assert "kubernetes peer fallback failed: kube down" in artifact.limitations
    assert "prometheus peer failed: prom down" in result.execution.execution_notes
    assert "kubernetes peer fallback failed: kube down" in result.execution.execution_notes


def test_advance_active_evidence_batch_still_rejects_node_batch_without_attempt_metadata() -> None:
    deps = _deps([])
    deps = PlannerDeps(
        **{
            **deps.__dict__,
            "scope_from_target": lambda target, profile: "node",
        }
    )
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(target="node/worker3", profile="workload", node_name="worker3"),
        deps,
    )

    with pytest.raises(ValueError, match="step collect-target-evidence requires evidence_bundle payload"):
        advance_active_evidence_batch(
            plan=plan,
            incident=BuildInvestigationPlanRequest(target="node/worker3", profile="workload", node_name="worker3"),
            submitted_steps=[
                SubmittedStepArtifact(
                    step_id="collect-target-evidence",
                    actual_route=ActualRoute(
                        source_kind="peer_mcp",
                        mcp_server="prometheus-mcp-server",
                        tool_name=None,
                        tool_path=["prometheus-mcp-server"],
                    ),
                    attempted_routes=[
                        ActualRoute(
                            source_kind="peer_mcp",
                            mcp_server="prometheus-mcp-server",
                            tool_name=None,
                            tool_path=["prometheus-mcp-server"],
                        )
                    ],
                    limitations=[],
                )
            ],
            batch_id="batch-1",
            deps=deps,
        )


def test_update_investigation_plan_unlocks_analysis_after_first_batch() -> None:
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="deployment/api"),
        _deps([]),
    )

    updated = update_investigation_plan(
        UpdateInvestigationPlanRequest(
            plan=plan,
            execution=EvidenceBatchExecution(
                batch_id="batch-1",
                executed_step_ids=["collect-target-evidence", "collect-change-candidates"],
                artifacts=[],
            ),
        )
    )

    rank_step = next(step for step in updated.steps if step.id == "rank-hypotheses")
    render_step = next(step for step in updated.steps if step.id == "render-report")
    assert updated.active_batch_id is None
    assert rank_step.status == "pending"
    assert render_step.status == "deferred"
    assert next(batch for batch in updated.evidence_batches if batch.id == "batch-2").status == "pending"


def test_update_investigation_plan_inserts_one_service_follow_up_for_inconclusive_workload_evidence() -> None:
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="deployment/api", service_name="api"),
        _deps([]),
    )

    updated = update_investigation_plan(
        UpdateInvestigationPlanRequest(
            plan=plan,
            execution=EvidenceBatchExecution(
                batch_id="batch-1",
                executed_step_ids=["collect-target-evidence", "collect-change-candidates"],
                artifacts=[
                    {
                        "step_id": "collect-target-evidence",
                        "plane": "workload",
                        "artifact_type": "evidence_bundle",
                        "evidence_bundle": _bundle(
                            findings=[
                                Finding(
                                    severity="info",
                                    source="heuristic",
                                    title="No Critical Signals Found",
                                    evidence="nothing decisive",
                                )
                            ]
                        ),
                        "summary": ["No Critical Signals Found"],
                        "limitations": [],
                    }
                ],
            ),
        )
    )

    follow_up = next(step for step in updated.steps if step.id == "collect-service-follow-up-evidence")
    rank_step = next(step for step in updated.steps if step.id == "rank-hypotheses")
    assert updated.active_batch_id == "batch-follow-up-service"
    assert follow_up.status == "pending"
    assert follow_up.depends_on == ["collect-target-evidence"]
    assert "collect-service-follow-up-evidence" in rank_step.depends_on
    assert rank_step.status == "deferred"


def test_update_investigation_plan_does_not_insert_service_follow_up_for_adequate_workload_evidence() -> None:
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(namespace="default", target="deployment/api", service_name="api"),
        _deps([]),
    )

    updated = update_investigation_plan(
        UpdateInvestigationPlanRequest(
            plan=plan,
            execution=EvidenceBatchExecution(
                batch_id="batch-1",
                executed_step_ids=["collect-target-evidence", "collect-change-candidates"],
                artifacts=[
                    {
                        "step_id": "collect-target-evidence",
                        "plane": "workload",
                        "artifact_type": "evidence_bundle",
                        "evidence_bundle": _bundle(
                            findings=[
                                Finding(
                                    severity="critical",
                                    source="k8s",
                                    title="CrashLoopBackOff",
                                    evidence="pod is crash looping",
                                )
                            ]
                        ),
                        "summary": ["CrashLoopBackOff"],
                        "limitations": [],
                    }
                ],
            ),
        )
    )

    assert updated.active_batch_id is None
    assert all(step.id != "collect-service-follow-up-evidence" for step in updated.steps)
    assert next(batch for batch in updated.evidence_batches if batch.id == "batch-2").status == "pending"


def test_update_investigation_plan_does_not_insert_service_follow_up_for_node_evidence() -> None:
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(target="node/worker3", node_name="worker3"),
        _deps([]),
    )

    updated = update_investigation_plan(
        UpdateInvestigationPlanRequest(
            plan=plan,
            execution=EvidenceBatchExecution(
                batch_id="batch-1",
                executed_step_ids=["collect-target-evidence", "collect-change-candidates"],
                artifacts=[
                    {
                        "step_id": "collect-target-evidence",
                        "plane": "node",
                        "artifact_type": "evidence_bundle",
                        "evidence_bundle": _bundle(
                            kind="node",
                            name="worker3",
                            findings=[
                                Finding(
                                    severity="warning",
                                    source="prometheus",
                                    title="High Node Memory Request Saturation",
                                    evidence="Memory requests are at 90.0% of allocatable capacity",
                                )
                            ],
                        ),
                        "summary": ["High Node Memory Request Saturation"],
                        "limitations": [],
                    }
                ],
            ),
        )
    )

    assert updated.active_batch_id is None
    assert all(step.id != "collect-service-follow-up-evidence" for step in updated.steps)


def test_execute_investigation_step_rejects_factual_mode_for_slice_two() -> None:
    plan = build_investigation_plan(
        BuildInvestigationPlanRequest(objective="factual", question="What uses the most CPU?"),
        _deps([]),
    )

    with pytest.raises(ValueError, match="not supported for factual_analysis"):
        execute_investigation_step(
            ExecuteInvestigationStepRequest(
                plan=plan,
                incident=BuildInvestigationPlanRequest(objective="factual", question="What uses the most CPU?"),
            ),
            _deps([]),
        )


def test_resolve_primary_target_preserves_requested_target() -> None:
    deps = _deps([])
    deps = PlannerDeps(
        **{
            **deps.__dict__,
            "canonical_target": lambda target, profile, service_name: "Backend/api",
            "scope_from_target": lambda target, profile: "workload",
        }
    )

    target = resolve_primary_target(
        InvestigationReportRequest(namespace="default", target="Backend/api"),
        deps,
    )

    assert target.requested_target == "Backend/api"
    assert target.target == "deployment/api"
    assert target.service_name == "api"
