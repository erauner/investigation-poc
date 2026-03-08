from investigation_service.models import BuildInvestigationPlanRequest, InvestigationReportRequest
from investigation_service.planner import (
    PlannerDeps,
    build_investigation_plan,
    classify_investigation_mode,
    resolve_primary_target,
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
        collect_service_context=lambda req: (_ for _ in ()).throw(AssertionError("planning must not collect service context")),
        collect_workload_context=lambda req: (_ for _ in ()).throw(AssertionError("planning must not collect workload context")),
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
    assert [step.id for step in plan.steps] == ["collect-factual-evidence", "summarize-findings"]
    assert calls == []


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
