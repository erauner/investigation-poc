from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityPolicy:
    capability: str
    preferred_mcp_server: str | None = None
    preferred_tool_names: tuple[str, ...] = ()
    fallback_mcp_server: str | None = None
    fallback_tool_names: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class BoundedExplorationPolicy:
    capability: str
    enabled: bool = False
    max_additional_pods: int = 0
    max_additional_probe_runs: int = 0
    max_metric_families: int = 0
    max_related_pods: int = 0


_POLICIES: dict[str, CapabilityPolicy] = {
    "alert_evidence_plane": CapabilityPolicy(
        capability="alert_evidence_plane",
        notes="Alert extraction and alert-shaped context remain internal product-owned control-plane behavior before peer evidence-plane drill-down.",
    ),
    "workload_evidence_plane": CapabilityPolicy(
        capability="workload_evidence_plane",
        preferred_mcp_server="kubernetes-mcp-server",
        preferred_tool_names=("pods_log", "resources_get", "events_list", "pods_list_in_namespace"),
        notes="Use Kubernetes runtime evidence first for workload-specific failures.",
    ),
    "service_evidence_plane": CapabilityPolicy(
        capability="service_evidence_plane",
        preferred_mcp_server="prometheus-mcp-server",
        preferred_tool_names=("execute_query", "execute_range_query"),
        fallback_mcp_server="kubernetes-mcp-server",
        fallback_tool_names=("resources_get", "events_list", "pods_list_in_namespace"),
        notes="Use metrics-first evidence for service symptoms, then fall back to Kubernetes runtime inspection if needed.",
    ),
    "node_evidence_plane": CapabilityPolicy(
        capability="node_evidence_plane",
        preferred_mcp_server="prometheus-mcp-server",
        preferred_tool_names=("execute_query", "execute_range_query"),
        fallback_mcp_server="kubernetes-mcp-server",
        fallback_tool_names=("resources_get", "events_list", "resources_list"),
        notes="Use metrics-first evidence for node capacity and pressure, then fall back to Kubernetes inspection if needed.",
    ),
    "collect_change_candidates": CapabilityPolicy(
        capability="collect_change_candidates",
        preferred_mcp_server="investigation-mcp-server",
        preferred_tool_names=("collect_change_candidates",),
        notes="Change correlation remains product-owned.",
    ),
    "rank_hypotheses": CapabilityPolicy(
        capability="rank_hypotheses",
        preferred_mcp_server="investigation-mcp-server",
        preferred_tool_names=("rank_hypotheses",),
        notes="Hypothesis ranking remains product-owned.",
    ),
    "render_investigation_report": CapabilityPolicy(
        capability="render_investigation_report",
        preferred_mcp_server="investigation-mcp-server",
        preferred_tool_names=("render_investigation_report",),
        notes="Final report rendering remains product-owned.",
    ),
}


_BOUNDED_EXPLORATION_POLICIES: dict[str, BoundedExplorationPolicy] = {
    "workload_evidence_plane": BoundedExplorationPolicy(
        capability="workload_evidence_plane",
        enabled=True,
        max_additional_pods=1,
        max_additional_probe_runs=1,
    ),
    "service_evidence_plane": BoundedExplorationPolicy(
        capability="service_evidence_plane",
        enabled=True,
        max_additional_probe_runs=1,
        max_metric_families=2,
    ),
    "node_evidence_plane": BoundedExplorationPolicy(
        capability="node_evidence_plane",
        enabled=True,
        max_additional_probe_runs=1,
        max_related_pods=5,
    ),
}


def policy_for_capability(capability: str | None) -> CapabilityPolicy | None:
    if capability is None:
        return None
    return _POLICIES.get(capability)


def policy_fields(capability: str | None) -> dict[str, str | list[str] | None]:
    policy = policy_for_capability(capability)
    if policy is None:
        return {
            "preferred_mcp_server": None,
            "preferred_tool_names": [],
            "fallback_mcp_server": None,
            "fallback_tool_names": [],
        }
    return {
        "preferred_mcp_server": policy.preferred_mcp_server,
        "preferred_tool_names": list(policy.preferred_tool_names),
        "fallback_mcp_server": policy.fallback_mcp_server,
        "fallback_tool_names": list(policy.fallback_tool_names),
    }


def bounded_exploration_policy_for_capability(capability: str | None) -> BoundedExplorationPolicy | None:
    if capability is None:
        return None
    return _BOUNDED_EXPLORATION_POLICIES.get(capability)
