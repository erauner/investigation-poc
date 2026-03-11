from investigation_service.exploration import (
    ScoutBudgetUsage,
    build_bounded_scout_observation,
    build_baseline_evidence_summary,
    build_exploratory_scout_context,
)
from investigation_service.models import (
    EvidenceStepContract,
    Finding,
    StepExecutionInputs,
    SubmittedStepArtifact,
    TargetRef,
)


def _artifact(*, findings=None, limitations=None, metrics=None, log_excerpt="", kind="deployment", name="api"):
    return SubmittedStepArtifact(
        step_id="collect-target-evidence",
        actual_route={
            "source_kind": "peer_mcp",
            "mcp_server": "kubernetes-mcp-server",
            "tool_name": "resources_get",
            "tool_path": ["kubernetes-mcp-server", "resources_get"],
        },
        evidence_bundle={
            "cluster": "erauner-home",
            "target": TargetRef(namespace="operator-smoke", kind=kind, name=name),
            "object_state": {"kind": kind, "name": name, "namespace": "operator-smoke"},
            "events": [],
            "log_excerpt": log_excerpt,
            "metrics": metrics or {},
            "findings": findings or [],
            "limitations": limitations or [],
            "enrichment_hints": ["inspect chronology"],
        },
    )


def _step(*, capability: str, plane: str, target: str) -> EvidenceStepContract:
    return EvidenceStepContract(
        step_id="collect-target-evidence",
        title=f"Collect {plane} evidence",
        plane=plane,
        artifact_type="evidence_bundle",
        requested_capability=capability,
        preferred_mcp_server="peer",
        preferred_tool_names=["tool"],
        fallback_mcp_server=None,
        fallback_tool_names=[],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="target_context",
            cluster="erauner-home",
            namespace="operator-smoke",
            target=target,
            profile="workload" if plane != "service" else "service",
            service_name="api" if plane == "service" else None,
            node_name="worker3" if plane == "node" else None,
            lookback_minutes=15,
        ),
    )


def test_build_baseline_evidence_summary_excludes_bookkeeping_metrics() -> None:
    summary = build_baseline_evidence_summary(
        _artifact(
            findings=[
                Finding(
                    severity="warning",
                    source="events",
                    title="Crash Loop Detected",
                    evidence="backoff seen in events",
                )
            ],
            metrics={
                "profile": "workload",
                "lookback_minutes": 15,
                "prometheus_available": True,
                "service_error_rate": 0.5,
            },
            log_excerpt="boom",
        ).evidence_bundle
    )

    assert summary.available_metric_keys == ("service_error_rate",)
    assert summary.has_log_excerpt is True
    assert summary.enrichment_hints == ("inspect chronology",)


def test_build_exploratory_scout_context_for_weak_workload_baseline() -> None:
    context = build_exploratory_scout_context(
        step=_step(capability="workload_evidence_plane", plane="workload", target="deployment/api"),
        artifact=_artifact(
            findings=[
                Finding(
                    severity="info",
                    source="heuristic",
                    title="No Critical Signals Found",
                    evidence="nothing decisive",
                )
            ],
            limitations=["logs unavailable"],
        ),
    )

    assert context is not None
    assert context.hints.adequacy_outcome == "weak"
    assert context.hints.preferred_probe_order == ("alternate_runtime_pod",)
    assert context.baseline_summary.has_log_excerpt is False


def test_build_exploratory_scout_context_for_service_and_node() -> None:
    service_context = build_exploratory_scout_context(
        step=_step(capability="service_evidence_plane", plane="service", target="service/api"),
        artifact=_artifact(
            kind="service",
            findings=[
                Finding(
                    severity="info",
                    source="heuristic",
                    title="No Critical Signals Found",
                    evidence="nothing decisive",
                )
            ],
            metrics={"service_error_rate": None, "prometheus_available": False},
        ),
    )
    node_context = build_exploratory_scout_context(
        step=_step(capability="node_evidence_plane", plane="node", target="node/worker3"),
        artifact=_artifact(
            kind="node",
            name="worker3",
            findings=[
                Finding(
                    severity="warning",
                    source="prometheus",
                    title="High Node Memory Request Saturation",
                    evidence="node requested memory is high",
                )
            ],
            metrics={"node_memory_request_bytes": 10},
        ),
    )

    assert service_context is not None
    assert service_context.hints.preferred_probe_order == ("service_range_metrics",)
    assert node_context is not None
    assert node_context.hints.preferred_probe_order == ("node_top_pods",)


def test_build_exploratory_scout_context_returns_none_for_adequate_or_attempt_only_artifacts() -> None:
    adequate_context = build_exploratory_scout_context(
        step=_step(capability="workload_evidence_plane", plane="workload", target="deployment/api"),
        artifact=_artifact(
            findings=[
                Finding(
                    severity="critical",
                    source="k8s",
                    title="CrashLoopBackOff",
                    evidence="pod is crash looping",
                )
            ]
        ),
    )
    attempt_only = SubmittedStepArtifact(
        step_id="collect-target-evidence",
        actual_route={
            "source_kind": "peer_mcp",
            "mcp_server": "kubernetes-mcp-server",
            "tool_name": None,
            "tool_path": ["kubernetes-mcp-server"],
        },
        limitations=["peer failed"],
    )

    assert adequate_context is None
    assert (
        build_exploratory_scout_context(
            step=_step(capability="workload_evidence_plane", plane="workload", target="deployment/api"),
            artifact=attempt_only,
        )
        is None
    )


def test_build_bounded_scout_observation_uses_context_and_budget() -> None:
    context = build_exploratory_scout_context(
        step=_step(capability="workload_evidence_plane", plane="workload", target="deployment/api"),
        artifact=_artifact(
            findings=[],
            limitations=["logs unavailable"],
        ),
    )

    observation = build_bounded_scout_observation(
        context=context,
        probe_kind="alternate_runtime_pod",
        stop_reason="probe_failed",
        budget_usage=ScoutBudgetUsage(
            probe_runs_used=1,
            additional_pods_used=1,
        ),
    )

    assert observation.capability == "workload_evidence_plane"
    assert observation.step_id == "collect-target-evidence"
    assert observation.probe_kind == "alternate_runtime_pod"
    assert observation.baseline_outcome == context.baseline_assessment.outcome
    assert observation.baseline_reasons == tuple(context.baseline_assessment.reasons)
    assert observation.stop_reason == "probe_failed"
    assert observation.budget_usage.probe_runs_used == 1
    assert observation.budget_usage.additional_pods_used == 1
