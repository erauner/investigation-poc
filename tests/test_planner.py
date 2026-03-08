from investigation_service.models import InvestigationReportRequest, TargetRef
from investigation_service.planner import (
    PlannerDeps,
    classify_investigation_mode,
    plan_investigation,
    resolve_primary_target,
)


def test_classify_investigation_mode_detects_alert_requests() -> None:
    mode = classify_investigation_mode(
        InvestigationReportRequest(
            alertname="PodCrashLooping",
            labels={"namespace": "default", "pod": "api-123"},
        )
    )

    assert mode == "alert"


def test_classify_investigation_mode_defaults_to_generic_requests() -> None:
    mode = classify_investigation_mode(InvestigationReportRequest(target="pod/api"))

    assert mode == "generic"


def test_plan_investigation_runs_policy_pipeline_and_aligns_cluster_note() -> None:
    req = InvestigationReportRequest(namespace="default", target="pod/api")
    steps: list[str] = []

    def fake_collect_context(req):
        steps.append("collect")
        assert req.target == "pod/api"
        return type(
            "Context",
            (),
            {
                "cluster": "erauner-home",
                "target": TargetRef(namespace="default", kind="pod", name="api-7f9d6"),
            },
        )()

    deps = PlannerDeps(
        normalize_alert_input=lambda req: (_ for _ in ()).throw(AssertionError(f"unexpected alert normalization: {req}")),
        canonical_target=lambda target, profile, service_name: target,
        scope_from_target=lambda target, profile: "workload",
        resolve_cluster=lambda cluster: (_ for _ in ()).throw(AssertionError(f"unexpected cluster resolution: {cluster}")),
        get_backend_cr=lambda *args, **kwargs: {},
        get_frontend_cr=lambda *args, **kwargs: {},
        get_cluster_cr=lambda *args, **kwargs: {},
        find_unhealthy_pod=lambda req: (_ for _ in ()).throw(AssertionError(f"unexpected unhealthy pod lookup: {req}")),
        collect_node_context=lambda req: (_ for _ in ()).throw(AssertionError(f"unexpected node context: {req}")),
        collect_service_context=lambda req: (_ for _ in ()).throw(AssertionError(f"unexpected service context: {req}")),
        collect_workload_context=fake_collect_context,
    )

    plan = plan_investigation(req, deps)

    assert plan.mode == "generic"
    assert plan.target.requested_target == "pod/api"
    assert plan.normalized.target == "pod/api-7f9d6"
    assert plan.target.target == "pod/api-7f9d6"
    assert plan.evidence.target.name == "api-7f9d6"
    assert "cluster resolved from collected context: erauner-home" in plan.normalized.normalization_notes
    assert steps == ["collect"]


def test_plan_investigation_uses_injected_dependency_container() -> None:
    calls: list[str] = []

    deps = PlannerDeps(
        normalize_alert_input=lambda req: (_ for _ in ()).throw(AssertionError(f"unexpected alert normalization: {req}")),
        canonical_target=lambda target, profile, service_name: calls.append("canonical_target") or "Backend/api",
        scope_from_target=lambda target, profile: calls.append("scope_from_target") or "workload",
        resolve_cluster=lambda cluster: calls.append("resolve_cluster") or type("ResolvedCluster", (), {"alias": "erauner-home"})(),
        get_backend_cr=lambda namespace, name, cluster=None: calls.append("get_backend_cr") or {"metadata": {"name": name}},
        get_frontend_cr=lambda namespace, name, cluster=None: calls.append("get_frontend_cr") or {},
        get_cluster_cr=lambda namespace, name, cluster=None: calls.append("get_cluster_cr") or {},
        find_unhealthy_pod=lambda req: calls.append("find_unhealthy_pod") or None,
        collect_node_context=lambda req: calls.append("collect_node_context") or None,
        collect_service_context=lambda req: calls.append("collect_service_context") or None,
        collect_workload_context=lambda req: calls.append("collect_workload_context")
        or type("Context", (), {"cluster": "erauner-home", "target": TargetRef(namespace="default", kind="deployment", name="api")})(),
    )

    plan = plan_investigation(InvestigationReportRequest(namespace="default", target="Backend/api"), deps)

    assert plan.normalized.target == "deployment/api"
    assert plan.normalized.cluster == "erauner-home"
    assert calls == [
        "canonical_target",
        "scope_from_target",
        "resolve_cluster",
        "get_backend_cr",
        "collect_workload_context",
    ]


def test_resolve_primary_target_preserves_requested_target() -> None:
    deps = PlannerDeps(
        normalize_alert_input=lambda req: (_ for _ in ()).throw(AssertionError(f"unexpected alert normalization: {req}")),
        canonical_target=lambda target, profile, service_name: "Backend/api",
        scope_from_target=lambda target, profile: "workload",
        resolve_cluster=lambda cluster: type("ResolvedCluster", (), {"alias": "erauner-home"})(),
        get_backend_cr=lambda namespace, name, cluster=None: {"metadata": {"name": name}},
        get_frontend_cr=lambda namespace, name, cluster=None: {},
        get_cluster_cr=lambda namespace, name, cluster=None: {},
        find_unhealthy_pod=lambda req: None,
        collect_node_context=lambda req: None,
        collect_service_context=lambda req: None,
        collect_workload_context=lambda req: None,
    )

    target = resolve_primary_target(InvestigationReportRequest(namespace="default", target="Backend/api"), deps)

    assert target.requested_target == "Backend/api"
    assert target.target == "deployment/api"
    assert target.service_name == "api"
