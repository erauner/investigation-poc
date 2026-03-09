import json
import logging
from collections.abc import Callable
from typing import Any


_LOGGER = logging.getLogger("investigation_service.mcp_tools")


def _ensure_logger() -> logging.Logger:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        _LOGGER.setLevel(logging.INFO)
        _LOGGER.propagate = True
        return _LOGGER
    if not _LOGGER.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        _LOGGER.addHandler(handler)
        _LOGGER.setLevel(logging.INFO)
        _LOGGER.propagate = False
    return _LOGGER


def _present_keys(raw_args: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key, value in raw_args.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)) and not value:
            continue
        if isinstance(value, str) and not value:
            continue
        keys.append(key)
    return sorted(keys)


def _incident_summary(incident: dict[str, Any] | None) -> dict[str, Any]:
    incident = incident or {}
    return {
        "has_incident": bool(incident),
        "incident_has_target": bool(incident.get("target")),
        "incident_has_alertname": bool(incident.get("alertname")),
        "incident_has_namespace": bool(incident.get("namespace")),
        "incident_profile": incident.get("profile"),
    }


def _execution_context_summary(execution_context: dict[str, Any] | None) -> dict[str, Any]:
    execution_context = execution_context or {}
    executions = execution_context.get("executions")
    return {
        "has_execution_context": bool(execution_context),
        "has_updated_plan": bool(execution_context.get("updated_plan")),
        "execution_count": len(executions) if isinstance(executions, list) else 0,
        "allow_bounded_fallback_execution": execution_context.get("allow_bounded_fallback_execution"),
    }


def _plan_summary(plan: dict[str, Any] | None) -> dict[str, Any]:
    plan = plan or {}
    steps = plan.get("steps")
    return {
        "plan_present": bool(plan),
        "plan_mode": plan.get("mode"),
        "plan_step_count": len(steps) if isinstance(steps, list) else 0,
        "plan_has_active_batch": bool(plan.get("active_batch_id")),
    }


def summarize_tool_inputs(tool_name: str, raw_args: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"present_keys": _present_keys(raw_args)}

    common = {
        "has_target": bool(raw_args.get("target")),
        "has_alertname": bool(raw_args.get("alertname")),
        "has_namespace": bool(raw_args.get("namespace")),
        "has_node_name": bool(raw_args.get("node_name")),
        "has_service_name": bool(raw_args.get("service_name")),
        "labels_count": len(raw_args.get("labels") or {}),
        "annotations_count": len(raw_args.get("annotations") or {}),
        "profile": raw_args.get("profile"),
    }

    if tool_name in {
        "get_active_evidence_batch",
        "submit_evidence_step_artifacts",
        "execute_investigation_step",
        "update_investigation_plan",
    }:
        summary.update(_plan_summary(raw_args.get("plan")))
        summary.update(_incident_summary(raw_args.get("incident")))
        if tool_name == "submit_evidence_step_artifacts":
            summary["submitted_steps_count"] = len(raw_args.get("submitted_steps") or [])
        if tool_name == "update_investigation_plan":
            execution = raw_args.get("execution") or {}
            summary["execution_step_count"] = len(execution.get("executed_step_ids") or [])
            summary["execution_artifact_count"] = len(execution.get("artifacts") or [])
        summary["has_batch_id"] = bool(raw_args.get("batch_id"))
        return summary

    if tool_name in {
        "advance_investigation_runtime",
        "handoff_active_evidence_batch",
    }:
        summary.update(_incident_summary(raw_args.get("incident")))
        summary.update(_execution_context_summary(raw_args.get("execution_context")))
        summary["submitted_steps_count"] = len(raw_args.get("submitted_steps") or [])
        summary["has_batch_id"] = bool(raw_args.get("batch_id"))
        return summary

    summary.update(common)
    return summary


def run_logged_tool(tool_name: str, raw_args: dict[str, Any], call: Callable[[], Any]) -> Any:
    logger = _ensure_logger()
    summary = summarize_tool_inputs(tool_name, raw_args)
    encoded_summary = json.dumps(summary, sort_keys=True)
    try:
        result = call()
    except Exception as exc:
        logger.warning(
            "mcp_tool_call tool=%s status=failure error_type=%s summary=%s",
            tool_name,
            type(exc).__name__,
            encoded_summary,
        )
        raise
    logger.info("mcp_tool_call tool=%s status=success summary=%s", tool_name, encoded_summary)
    return result
