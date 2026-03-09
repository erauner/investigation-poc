import pytest

from investigation_service.models import (
    BuildInvestigationPlanRequest,
    CorrelatedChangesResponse,
    EvidenceBatchExecution,
    EvidenceBundle,
    ExecuteInvestigationStepRequest,
    Finding,
    InvestigationPlan,
    InvestigationReportRequest,
    NormalizedInvestigationRequest,
    TargetRef,
    UpdateInvestigationPlanRequest,
)
from investigation_service.planner import (
    PlannerDeps,
    build_investigation_plan,
    classify_investigation_mode,
    execute_investigation_step,
    resolve_primary_target,
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
        collect_node_context=lambda req: (_ for _ in ()).throw(AssertionError("planning must not collect node context")),
        collect_service_context=lambda req: (_ for _ in ()).throw(
            AssertionError("planning must not collect service context")
        ),
        collect_workload_context=lambda req: (_ for _ in ()).throw(
            AssertionError("planning must not collect workload context")
        ),
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
    assert [batch.id for batch in plan.evidence_batches] == ["batch-1", "batch-2", "batch-3"]
    assert calls == ["canonical_target", "scope_from_target"]


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
    assert calls == ["canonical_target", "scope_from_target", "resolve_cluster", "get_backend_cr"]


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
    assert "collect_alert_evidence" in calls


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
