from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .settings import (
    get_cluster_name,
    get_cluster_registry_path,
    get_default_cluster_alias,
    get_kubeconfig_path,
    get_prometheus_url,
)


class ClusterConfig(BaseModel):
    alias: str
    kube_context: str | None = None
    prometheus_url: str | None = None
    label_aliases: list[str] = Field(default_factory=list)
    default: bool = False
    allowed_namespaces: list[str] | None = None


class ResolvedCluster(BaseModel):
    alias: str
    kube_context: str | None = None
    kubeconfig_path: str | None = None
    prometheus_url: str | None = None
    source: str
    allowed_namespaces: list[str] | None = None


class ClusterRegistry(BaseModel):
    clusters: dict[str, ClusterConfig] = Field(default_factory=dict)
    default_cluster: str | None = None


def _normalize_alias(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _registry_file() -> dict:
    path = get_cluster_registry_path()
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.exists():
        return {}
    try:
        return yaml.safe_load(candidate.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _resolved_kubeconfig_path() -> str | None:
    path = get_kubeconfig_path()
    if not path:
        return None
    candidate = Path(path)
    if not candidate.exists() or not candidate.is_file():
        return None
    return str(candidate)


def load_cluster_registry() -> ClusterRegistry:
    raw = _registry_file()
    clusters: dict[str, ClusterConfig] = {}
    for alias, config in (raw.get("clusters") or {}).items():
        normalized = _normalize_alias(alias)
        if not normalized:
            continue
        item = ClusterConfig.model_validate(
            {
                "alias": normalized,
                **(config or {}),
            }
        )
        item.alias = normalized
        clusters[normalized] = item

    default_cluster = _normalize_alias(get_default_cluster_alias() or raw.get("default_cluster"))
    if not default_cluster:
        default_cluster = next((alias for alias, item in clusters.items() if item.default), None)
    return ClusterRegistry(clusters=clusters, default_cluster=default_cluster)


def list_clusters() -> list[str]:
    return sorted(load_cluster_registry().clusters.keys())


def _resolve_registered_cluster(config: ClusterConfig, source: str) -> ResolvedCluster:
    return ResolvedCluster(
        alias=config.alias,
        kube_context=config.kube_context,
        kubeconfig_path=_resolved_kubeconfig_path(),
        prometheus_url=config.prometheus_url or get_prometheus_url(),
        source=source,
        allowed_namespaces=config.allowed_namespaces,
    )


def _legacy_cluster() -> ResolvedCluster:
    return ResolvedCluster(
        alias=_normalize_alias(get_cluster_name()) or "current-context",
        kube_context=None,
        kubeconfig_path=_resolved_kubeconfig_path(),
        prometheus_url=get_prometheus_url(),
        source="legacy_current_context",
        allowed_namespaces=None,
    )


def resolve_cluster(requested_cluster: str | None, labels: dict[str, str] | None = None) -> ResolvedCluster:
    registry = load_cluster_registry()
    requested_alias = _normalize_alias(requested_cluster)
    if requested_alias:
        config = registry.clusters.get(requested_alias)
        if config is None:
            raise ValueError(f"unknown cluster alias: {requested_cluster}")
        return _resolve_registered_cluster(config, "explicit")

    labels = labels or {}
    candidate_labels = [
        labels.get("cluster"),
        labels.get("cluster_name"),
        labels.get("kubernetes_cluster"),
    ]
    for value in candidate_labels:
        normalized = _normalize_alias(value)
        if not normalized:
            continue
        direct = registry.clusters.get(normalized)
        if direct is not None:
            return _resolve_registered_cluster(direct, "alert_label")
        for config in registry.clusters.values():
            if normalized in {_normalize_alias(alias) for alias in config.label_aliases}:
                return _resolve_registered_cluster(config, "alert_label")
        raise ValueError(f"unknown cluster alias from alert labels: {value}")

    if registry.default_cluster:
        config = registry.clusters.get(registry.default_cluster)
        if config is None:
            raise ValueError(f"default cluster alias is not configured: {registry.default_cluster}")
        return _resolve_registered_cluster(config, "default")

    if registry.clusters:
        raise ValueError("cluster is required because multiple clusters are configured and no default is set")

    return _legacy_cluster()
