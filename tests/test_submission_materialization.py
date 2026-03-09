from investigation_service.models import (
    ActualRoute,
    CorrelatedChangesResponse,
    EvidenceBundle,
    EvidenceStepContract,
    Finding,
    StepExecutionInputs,
    TargetRef,
)
from investigation_service.submission_materialization import materialize_submitted_step


def _route() -> ActualRoute:
    return ActualRoute(
        source_kind="peer_mcp",
        mcp_server="kubernetes-mcp-server",
        tool_name="pods_log",
        tool_path=["pods_log"],
    )


def _inputs() -> StepExecutionInputs:
    return StepExecutionInputs(
        request_kind="target_context",
        cluster="kind-investigation",
        namespace="operator-smoke",
        target="deployment/crashy",
        profile="workload",
    )


def _bundle() -> EvidenceBundle:
    return EvidenceBundle(
        cluster="kind-investigation",
        target=TargetRef(namespace="operator-smoke", kind="deployment", name="crashy"),
        object_state={"kind": "Deployment", "name": "crashy"},
        events=[],
        log_excerpt="CrashLoopBackOff",
        metrics={},
        findings=[
                Finding(
                    severity="critical",
                    source="k8s",
                    title="Crash loop detected",
                    evidence="Pod restarted repeatedly",
                )
        ],
        limitations=[],
        enrichment_hints=[],
    )


def test_materialize_submitted_step_builds_evidence_bundle_submission() -> None:
    step = EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect target evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        requested_capability="workload_evidence_plane",
        preferred_mcp_server="kubernetes-mcp-server",
        preferred_tool_names=["pods_log"],
        fallback_mcp_server="investigation-mcp-server",
        fallback_tool_names=["collect_workload_evidence"],
        execution_mode="external_preferred",
        execution_inputs=_inputs(),
    )

    submission = materialize_submitted_step(
        step,
        actual_route=_route(),
        evidence_bundle=_bundle(),
        summary=["Collected workload evidence from peer plane."],
    )

    assert submission.step_id == "collect-target-evidence"
    assert submission.evidence_bundle is not None
    assert submission.change_candidates is None
    assert submission.actual_route.mcp_server == "kubernetes-mcp-server"


def test_materialize_submitted_step_rejects_payload_mismatch() -> None:
    step = EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect target evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        requested_capability="workload_evidence_plane",
        execution_mode="external_preferred",
        execution_inputs=_inputs(),
    )

    changes = CorrelatedChangesResponse(
        cluster="kind-investigation",
        scope="workload",
        target="deployment/crashy",
        changes=[],
        limitations=[],
    )

    try:
        materialize_submitted_step(
            step,
            actual_route=_route(),
            change_candidates=changes,
        )
    except ValueError as exc:
        assert "requires evidence_bundle payload" in str(exc)
    else:
        raise AssertionError("expected mismatch to raise")
