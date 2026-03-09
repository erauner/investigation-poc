import logging

import pytest

from investigation_service.mcp_logging import run_logged_tool, summarize_tool_inputs


def test_summarize_runtime_tool_inputs_uses_flags_and_counts() -> None:
    summary = summarize_tool_inputs(
        "handoff_active_evidence_batch",
        {
            "incident": {
                "target": "pod/crashy",
                "alertname": "PodCrashLooping",
                "namespace": "operator-smoke",
                "profile": "workload",
            },
            "execution_context": {
                "updated_plan": {"mode": "alert_rca"},
                "executions": [{"batch_id": "batch-1"}],
                "allow_bounded_fallback_execution": False,
            },
            "handoff_token": "opaque-token",
            "submitted_steps": [{"step_id": "collect-target-evidence", "payload": {"logs": "secret"}}],
            "batch_id": "batch-1",
        },
    )

    assert summary["incident_has_target"] is True
    assert summary["incident_has_alertname"] is True
    assert summary["incident_has_namespace"] is True
    assert summary["incident_profile"] == "workload"
    assert summary["has_execution_context"] is True
    assert summary["has_handoff_token"] is True
    assert summary["handoff_token_length"] == len("opaque-token")
    assert summary["has_updated_plan"] is True
    assert summary["execution_count"] == 1
    assert summary["submitted_steps_count"] == 1
    assert summary["has_batch_id"] is True
    assert "payload" not in str(summary)
    assert "pod/crashy" not in str(summary)


def test_run_logged_tool_logs_success_without_dumping_raw_payload(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="investigation_service.mcp_tools")

    run_logged_tool(
        "render_investigation_report",
        {
            "target": "pod/crashy",
            "labels": {"pod": "crashy"},
            "annotations": {"summary": "CrashLoopBackOff"},
            "profile": "workload",
        },
        lambda: {"ok": True},
    )

    messages = [record.message for record in caplog.records]
    assert any("tool=render_investigation_report" in message for message in messages)
    assert any("status=success" in message for message in messages)
    assert not any("pod/crashy" in message for message in messages)
    assert not any("CrashLoopBackOff" in message for message in messages)


def test_run_logged_tool_logs_failure_type_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="investigation_service.mcp_tools")

    with pytest.raises(ValueError):
        run_logged_tool(
            "advance_investigation_runtime",
            {"incident": {"namespace": "operator-smoke"}, "execution_context": {}, "handoff_token": "opaque", "submitted_steps": []},
            lambda: (_ for _ in ()).throw(ValueError("incident target pod/crashy invalid")),
        )

    messages = [record.message for record in caplog.records]
    assert any("tool=advance_investigation_runtime" in message for message in messages)
    assert any("status=failure" in message for message in messages)
    assert any("error_type=ValueError" in message for message in messages)
    assert not any("incident target pod/crashy invalid" in message for message in messages)
