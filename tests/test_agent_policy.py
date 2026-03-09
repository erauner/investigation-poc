from pathlib import Path

import yaml

from investigation_service import mcp_server


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_INVESTIGATION_AGENT_TOOLS = {
    "resolve_primary_target",
    "build_investigation_plan",
    "get_active_evidence_batch",
    "submit_evidence_step_artifacts",
    "advance_investigation_runtime",
    "execute_investigation_step",
    "update_investigation_plan",
    "rank_hypotheses",
    "render_investigation_report",
    "collect_change_candidates",
}

EXPECTED_KUBERNETES_MCP_TOOLS = {
    "resources_list",
    "resources_get",
    "pods_list_in_namespace",
    "pods_log",
    "events_list",
    "namespaces_list",
}

EXPECTED_PROMETHEUS_MCP_TOOLS = {
    "execute_query",
    "execute_range_query",
    "get_targets",
    "get_rules",
    "get_alerts",
    "query_exemplars",
}

BANNED_AGENT_TOOLS = {
    "build_alert_investigation_report",
    "build_investigation_report",
    "normalize_alert_input",
    "find_unhealthy_pod",
    "find_unhealthy_workloads",
    "normalize_incident_input",
    "collect_workload_evidence",
    "collect_service_evidence",
    "collect_node_evidence",
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

PLANNER_LED_REQUIRED_PHRASES = (
    "build_investigation_plan",
    "get_active_evidence_batch",
    "submit_evidence_step_artifacts",
    "advance_investigation_runtime",
    "execute_investigation_step",
    "update_investigation_plan",
    "fallback/debug primitives",
    "Advance one active evidence batch at a time",
    "Do not render the final report as the first substantive step.",
    "Do not call advance_investigation_runtime with only batch_id.",
)

ALERT_CONTEXT_REQUIRED_PHRASE = (
    "preserve the original alert name and the resolved operational target name explicitly"
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
    kubernetes_tools = next(
        item["mcpServer"]["toolNames"]
        for item in tools
        if item["type"] == "McpServer" and item["mcpServer"]["name"] == "kubernetes-mcp-server"
    )
    prometheus_tools = next(
        item["mcpServer"]["toolNames"]
        for item in tools
        if item["type"] == "McpServer" and item["mcpServer"]["name"] == "prometheus-mcp-server"
    )

    assert set(investigation_tools) == EXPECTED_INVESTIGATION_AGENT_TOOLS
    assert set(kubernetes_tools) == EXPECTED_KUBERNETES_MCP_TOOLS
    assert set(prometheus_tools) == EXPECTED_PROMETHEUS_MCP_TOOLS
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
    for phrase in PLANNER_LED_REQUIRED_PHRASES:
        assert phrase in system_message
    assert ALERT_CONTEXT_REQUIRED_PHRASE in system_message.lower()


def test_local_and_packaged_wrappers_teach_planner_led_sequence() -> None:
    wrapper_paths = [
        ".claude/commands/investigate.md",
        ".claude/commands/investigate-alert.md",
        ".claude/skills/investigation-helper/SKILL.md",
        "claude-code-marketplace/investigation-tools/commands/investigate.md",
        "claude-code-marketplace/investigation-tools/commands/investigate-alert.md",
        "desktop-extension/server/index.js",
    ]
    required_phrases = [
        "build_investigation_plan",
        "get_active_evidence_batch",
        "submit_evidence_step_artifacts",
        "advance_investigation_runtime",
        "execute_investigation_step",
        "update_investigation_plan",
        "render_investigation_report",
        "fallback/debug primitives",
        "do not call it with only batch_id",
    ]
    banned_phrases = [
        "build_investigation_report",
        "build_alert_investigation_report",
        "find_unhealthy_pod",
    ]

    for path in wrapper_paths:
        text = (ROOT / path).read_text()
        for phrase in required_phrases:
            assert phrase in text, path
        if "alert" in path.lower():
            assert ALERT_CONTEXT_REQUIRED_PHRASE in text.lower(), path
        for phrase in banned_phrases:
            assert phrase not in text, path
