from pathlib import Path
import json
import subprocess

import yaml

from investigation_service import mcp_server
from investigation_service.execution_policy import policy_for_capability


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_INVESTIGATION_AGENT_TOOLS = {
    "run_orchestrated_investigation",
    "resolve_primary_target",
    "build_investigation_plan",
    "handoff_active_evidence_batch",
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
    "collect_alert_evidence",
    "collect_workload_context",
    "collect_service_context",
    "collect_node_context",
    "collect_alert_context",
    "build_root_cause_report",
    "collect_correlated_changes",
    "top-level report tool",
)

PLANNER_LED_REQUIRED_PHRASES = (
    "run_orchestrated_investigation",
    "build_investigation_plan",
    "handoff_active_evidence_batch",
    "execute_investigation_step",
    "update_investigation_plan",
    "fallback/debug primitives",
    "Do not render the final report as the first substantive step.",
    "fine-grained runtime seams",
    "## Recommended next step",
    "default end-to-end runtime path",
    "external-step materialization",
)

ALERT_CONTEXT_REQUIRED_PHRASE = (
    "preserve the original alert name and the resolved operational target name explicitly"
)
ALERT_TARGET_VERBATIM_REQUIRED_PHRASE = (
    "preserve the exact original alert-derived target string verbatim"
)
ALERT_EXTRACTION_REQUIRED_PHRASE = (
    "extract alertname, labels, annotations, namespace, pod, service, instance, severity, and status"
)
ALERT_NAMESPACE_GUARDRAIL_REQUIRED_PHRASE = (
    "if a service or pod label is present but namespace is missing, say the namespace is unknown instead of guessing."
)
ALERT_FREEFORM_TARGET_GUARDRAIL_REQUIRED_PHRASE = (
    "do not investigate the first freeform words of the pasted message as the target unless they are explicitly a kubernetes object reference such as pod/<name> or service/<name>."
)
ALERT_FIVE_SECTION_REQUIRED_PHRASE = (
    "return exactly these five sections and no extra appendix sections: diagnosis, evidence, related data, limitations, recommended next step."
)
SHARED_RUNTIME_REQUIRED_PHRASES = (
    "Use the planner-led investigation flow.",
    "run_orchestrated_investigation keeps batch selection, external-step materialization, advancement, and final rendering in product code.",
    "Treat handoff_active_evidence_batch, get_active_evidence_batch, submit_evidence_step_artifacts, and advance_investigation_runtime as lower-level fine-grained runtime seams for debugging or explicit adapter choreography.",
    "Treat execute_investigation_step and update_investigation_plan as lower-level fallback/debug primitives.",
    "Use render_investigation_report only as a secondary low-level render seam when you are explicitly debugging the staged runtime path.",
    "Use exactly these Markdown headings verbatim: ## Diagnosis, ## Evidence, ## Related Data, ## Limitations, ## Recommended next step.",
)


def _load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text())


def test_k8s_kustomization_includes_agent_manifest() -> None:
    manifest = _load_yaml("k8s/kustomization.yaml")

    assert "agent.yaml" in manifest["resources"]


def test_shadow_agent_manifest_uses_byo_lane() -> None:
    manifest = _load_yaml("k8s/shadow/agent.yaml")

    assert manifest["spec"]["type"] == "BYO"
    assert manifest["metadata"]["name"] == "incident-triage-shadow"
    assert manifest["spec"]["byo"]["deployment"]["image"].startswith("ghcr.io/erauner/investigation-shadow-runtime:")


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
    assert "normalize_alert_input" not in exported_tools


def test_execution_policy_preferred_tool_names_are_backed_by_manifest_catalogs() -> None:
    manifest = _load_yaml("k8s/agent.yaml")
    tools = manifest["spec"]["declarative"]["tools"]
    kubernetes_tools = set(
        next(
            item["mcpServer"]["toolNames"]
            for item in tools
            if item["type"] == "McpServer" and item["mcpServer"]["name"] == "kubernetes-mcp-server"
        )
    )
    prometheus_tools = set(
        next(
            item["mcpServer"]["toolNames"]
            for item in tools
            if item["type"] == "McpServer" and item["mcpServer"]["name"] == "prometheus-mcp-server"
        )
    )

    workload_policy = policy_for_capability("workload_evidence_plane")
    service_policy = policy_for_capability("service_evidence_plane")
    node_policy = policy_for_capability("node_evidence_plane")
    alert_policy = policy_for_capability("alert_evidence_plane")

    assert set(workload_policy.preferred_tool_names) <= kubernetes_tools
    assert set(service_policy.preferred_tool_names) <= prometheus_tools
    assert set(node_policy.preferred_tool_names) <= prometheus_tools
    assert alert_policy is not None
    assert alert_policy.preferred_mcp_server is None
    assert alert_policy.preferred_tool_names == ()
    assert alert_policy.fallback_mcp_server is None
    assert alert_policy.fallback_tool_names == ()


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
    assert ALERT_TARGET_VERBATIM_REQUIRED_PHRASE in system_message.lower()


def test_local_and_packaged_wrappers_teach_planner_led_sequence() -> None:
    wrapper_paths = [
        ".claude/commands/investigate.md",
        ".claude/commands/investigate-alert.md",
        "claude-code-marketplace/investigation-tools/commands/investigate.md",
        "claude-code-marketplace/investigation-tools/commands/investigate-alert.md",
    ]
    required_phrases = [
        "run_orchestrated_investigation",
        "handoff_active_evidence_batch",
        "execute_investigation_step",
        "update_investigation_plan",
        "fallback/debug primitives",
        "fine-grained runtime seams",
        "default end-to-end runtime path",
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
        for phrase in SHARED_RUNTIME_REQUIRED_PHRASES:
            assert phrase in text, path
        if "alert" in path.lower():
            assert ALERT_CONTEXT_REQUIRED_PHRASE in text.lower(), path
            assert ALERT_TARGET_VERBATIM_REQUIRED_PHRASE in text.lower(), path
            assert ALERT_EXTRACTION_REQUIRED_PHRASE in text.lower(), path
            assert ALERT_NAMESPACE_GUARDRAIL_REQUIRED_PHRASE in text.lower(), path
            assert ALERT_FREEFORM_TARGET_GUARDRAIL_REQUIRED_PHRASE in text.lower(), path
            assert ALERT_FIVE_SECTION_REQUIRED_PHRASE in text.lower(), path
        else:
            assert ALERT_CONTEXT_REQUIRED_PHRASE not in text.lower(), path
            assert ALERT_TARGET_VERBATIM_REQUIRED_PHRASE not in text.lower(), path
            assert ALERT_EXTRACTION_REQUIRED_PHRASE not in text.lower(), path
            assert ALERT_NAMESPACE_GUARDRAIL_REQUIRED_PHRASE not in text.lower(), path
            assert ALERT_FREEFORM_TARGET_GUARDRAIL_REQUIRED_PHRASE not in text.lower(), path
            assert ALERT_FIVE_SECTION_REQUIRED_PHRASE not in text.lower(), path
        for phrase in banned_phrases:
            assert phrase not in text, path


def test_skill_and_desktop_extension_keep_shared_runtime_block_with_parse_only_alert_delta() -> None:
    wrapper_paths = [
        ".claude/skills/investigation-helper/SKILL.md",
        "desktop-extension/server/index.js",
    ]

    for path in wrapper_paths:
        text = (ROOT / path).read_text()
        for phrase in SHARED_RUNTIME_REQUIRED_PHRASES:
            assert phrase in text, path
        assert ALERT_CONTEXT_REQUIRED_PHRASE in text.lower(), path
        assert ALERT_TARGET_VERBATIM_REQUIRED_PHRASE in text.lower(), path
        assert ALERT_EXTRACTION_REQUIRED_PHRASE in text.lower(), path
        assert ALERT_NAMESPACE_GUARDRAIL_REQUIRED_PHRASE in text.lower(), path
        assert ALERT_FREEFORM_TARGET_GUARDRAIL_REQUIRED_PHRASE in text.lower(), path
        assert ALERT_FIVE_SECTION_REQUIRED_PHRASE in text.lower(), path
        assert "If the target is vague or operator-backed, resolve it first with resolve_primary_target." in text, path
        for phrase in BANNED_PROMPT_PHRASES:
            assert phrase not in text, path


def test_desktop_extension_emits_mode_specific_wrapper_content() -> None:
    script = f"""
import {{ buildInvestigationTask }} from {str((ROOT / "desktop-extension/server/index.js").resolve().as_uri())!r};
const genericTask = buildInvestigationTask({{task: "Investigate the unhealthy pod in namespace kagent-smoke."}});
const inferredAlertTask = buildInvestigationTask({{task: "Investigate alert PodCrashLooping for pod crashy in namespace kagent-smoke."}});
const explicitAlertTask = buildInvestigationTask({{task: "PodCrashLooping on crashy", mode: "alert", alertname: "PodCrashLooping"}});
process.stdout.write(JSON.stringify({{ genericTask, inferredAlertTask, explicitAlertTask }}));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert "[INVESTIGATION_ENTRYPOINT]=generic" in payload["genericTask"]
    assert ALERT_EXTRACTION_REQUIRED_PHRASE not in payload["genericTask"].lower()
    assert "[INVESTIGATION_ENTRYPOINT]=alert" in payload["inferredAlertTask"]
    assert ALERT_EXTRACTION_REQUIRED_PHRASE in payload["inferredAlertTask"].lower()
    assert ALERT_NAMESPACE_GUARDRAIL_REQUIRED_PHRASE in payload["inferredAlertTask"].lower()
    assert ALERT_FREEFORM_TARGET_GUARDRAIL_REQUIRED_PHRASE in payload["inferredAlertTask"].lower()
    assert ALERT_FIVE_SECTION_REQUIRED_PHRASE in payload["inferredAlertTask"].lower()
    assert "alertname: PodCrashLooping" in payload["explicitAlertTask"]
