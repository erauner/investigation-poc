from investigation_service import mcp_server
from investigation_service.models import (
    AdvanceInvestigationRuntimeResponse,
    ExplorationOutcome,
    HandoffActiveEvidenceBatchResponse,
    InvestigationPlan,
    ReportingExecutionContext,
    SubmittedEvidenceReconciliationResult,
)


def _context() -> ReportingExecutionContext:
    return ReportingExecutionContext(
        updated_plan=InvestigationPlan(
            mode="targeted_rca",
            objective="Investigate service/api",
            target=None,
            steps=[],
            evidence_batches=[],
            active_batch_id=None,
            planning_notes=[],
        ),
        executions=[],
        allow_bounded_fallback_execution=False,
    )


def _capture_run_logged_tool(captured: dict[str, object]):
    def _run_logged_tool(_name, payload, callback):
        captured["payload"] = payload
        return callback()

    return _run_logged_tool


def test_submit_evidence_step_artifacts_wrapper_forwards_exploration_outcomes(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_impl(req):
        captured["request"] = req
        return SubmittedEvidenceReconciliationResult(
            execution={
                "batch_id": "batch-1",
                "executed_step_ids": [],
                "artifacts": [],
                "execution_notes": [],
            },
            updated_plan=_context().updated_plan,
        )

    monkeypatch.setattr(mcp_server, "run_logged_tool", _capture_run_logged_tool(captured))
    monkeypatch.setattr(mcp_server, "submit_evidence_step_artifacts_impl", fake_impl)

    result = mcp_server.submit_evidence_step_artifacts(
        plan={
            "mode": "targeted_rca",
            "objective": "Investigate service/api",
            "target": None,
            "steps": [],
            "evidence_batches": [],
            "planning_notes": [],
        },
        incident={"namespace": "default", "target": "service/api", "profile": "service"},
        submitted_steps=[],
        exploration_outcomes=[
            {
                "step_id": "collect-target-evidence",
                "capability": "service_evidence_plane",
                "intent": "evidence_expansion",
                "outcome": "evidence_delta",
                "probe_kind": "service_range_metrics",
                "notes": ["probe_improved_artifact"],
            }
        ],
    )

    assert captured["payload"]["exploration_outcomes"][0]["probe_kind"] == "service_range_metrics"
    assert captured["request"].exploration_outcomes == [
        ExplorationOutcome(
            step_id="collect-target-evidence",
            capability="service_evidence_plane",
            intent="evidence_expansion",
            outcome="evidence_delta",
            probe_kind="service_range_metrics",
            notes=["probe_improved_artifact"],
        )
    ]
    assert result["updated_plan"]["objective"] == "Investigate service/api"


def test_advance_investigation_runtime_wrapper_forwards_exploration_outcomes(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_impl(req):
        captured["request"] = req
        return AdvanceInvestigationRuntimeResponse(
            execution_context=_context(),
            next_active_batch=None,
        )

    monkeypatch.setattr(mcp_server, "run_logged_tool", _capture_run_logged_tool(captured))
    monkeypatch.setattr(mcp_server, "advance_investigation_runtime_impl", fake_impl)

    result = mcp_server.advance_investigation_runtime(
        incident={"namespace": "default", "target": "service/api", "profile": "service"},
        exploration_outcomes=[
            {
                "step_id": "collect-target-evidence",
                "capability": "service_evidence_plane",
                "intent": "evidence_expansion",
                "outcome": "no_useful_change",
                "probe_kind": "service_range_metrics",
                "notes": ["probe_not_improving"],
            }
        ],
    )

    assert captured["payload"]["exploration_outcomes"][0]["outcome"] == "no_useful_change"
    assert captured["request"].exploration_outcomes[0].notes == ["probe_not_improving"]
    assert result["execution_context"]["allow_bounded_fallback_execution"] is False


def test_handoff_active_evidence_batch_wrapper_forwards_exploration_outcomes(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_impl(req):
        captured["request"] = req
        return HandoffActiveEvidenceBatchResponse(
            execution_context=_context(),
            handoff_token="opaque-token",
            active_batch=None,
            execution=None,
            handoff_status="complete",
            next_action="render_report",
            required_external_step_ids=[],
        )

    monkeypatch.setattr(mcp_server, "run_logged_tool", _capture_run_logged_tool(captured))
    monkeypatch.setattr(mcp_server, "handoff_active_evidence_batch_impl", fake_impl)

    result = mcp_server.handoff_active_evidence_batch(
        incident={"namespace": "default", "target": "service/api", "profile": "service"},
        exploration_outcomes=[
            {
                "step_id": "collect-target-evidence",
                "capability": "service_evidence_plane",
                "intent": "evidence_expansion",
                "outcome": "no_useful_change",
                "probe_kind": "service_range_metrics",
                "notes": ["probe_not_improving"],
            }
        ],
    )

    assert captured["payload"]["exploration_outcomes"][0]["notes"] == ["probe_not_improving"]
    assert captured["request"].exploration_outcomes[0].probe_kind == "service_range_metrics"
    assert result["handoff_token"] == "opaque-token"
