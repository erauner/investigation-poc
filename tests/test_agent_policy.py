from pathlib import Path

import yaml

from investigation_service import mcp_server


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_AGENT_TOOLS = {
    "normalize_incident_input",
    "resolve_primary_target",
    "build_investigation_plan",
    "execute_investigation_step",
    "update_investigation_plan",
    "rank_hypotheses",
    "render_investigation_report",
    "collect_workload_evidence",
    "collect_service_evidence",
    "collect_node_evidence",
    "collect_change_candidates",
}

BANNED_AGENT_TOOLS = {
    "build_alert_investigation_report",
    "build_investigation_report",
    "normalize_alert_input",
    "find_unhealthy_pod",
    "find_unhealthy_workloads",
    "collect_workload_context",
    "collect_service_context",
    "collect_node_context",
    "collect_alert_context",
    "collect_alert_evidence",
    "build_root_cause_report",
    "collect_correlated_changes",
}

BANNED_PROMPT_PHRASES = (
    "build_investigation_report",
    "build_alert_investigation_report",
    "find_unhealthy_pod",
    "find_unhealthy_workloads",
    "collect_workload_context",
    "collect_service_context",
    "collect_node_context",
    "collect_alert_context",
    "build_root_cause_report",
    "collect_correlated_changes",
    "top-level report tool",
)


def _load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text())


def test_k8s_kustomization_includes_agent_manifest() -> None:
    manifest = _load_yaml("k8s/kustomization.yaml")

    assert "agent.yaml" in manifest["resources"]


def test_agent_manifest_uses_narrow_planner_led_tool_catalog() -> None:
    manifest = _load_yaml("k8s/agent.yaml")
    tools = manifest["spec"]["declarative"]["tools"]
    investigation_tools = next(
        item["mcpServer"]["toolNames"]
        for item in tools
        if item["type"] == "McpServer" and item["mcpServer"]["name"] == "investigation-mcp-server"
    )

    assert set(investigation_tools) == EXPECTED_AGENT_TOOLS
    assert not (set(investigation_tools) & BANNED_AGENT_TOOLS)


def test_agent_manifest_tool_catalog_is_subset_of_exported_mcp_tools() -> None:
    manifest = _load_yaml("k8s/agent.yaml")
    tools = manifest["spec"]["declarative"]["tools"]
    investigation_tools = next(
        item["mcpServer"]["toolNames"]
        for item in tools
        if item["type"] == "McpServer" and item["mcpServer"]["name"] == "investigation-mcp-server"
    )
    exported_tools = set(mcp_server.mcp._tool_manager._tools.keys())

    assert set(investigation_tools) <= exported_tools


def test_skill_configmap_stops_teaching_report_first_or_hidden_tools() -> None:
    config = _load_yaml("k8s/investigation-skill-configmap.yaml")
    system_message = config["data"]["system-message"]

    assert "render_investigation_report" in system_message
    assert "resolve_primary_target" in system_message
    assert "build_investigation_plan" in system_message
    for phrase in BANNED_PROMPT_PHRASES:
        assert phrase not in system_message
