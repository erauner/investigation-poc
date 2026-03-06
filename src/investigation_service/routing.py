from .models import ScopeType


def scope_from_target(target: str, profile: str) -> ScopeType:
    if target.startswith("node/"):
        return "node"
    if target.startswith("service/") or profile == "service":
        return "service"
    if profile == "otel-pipeline":
        return "otel-pipeline"
    return "workload"


def canonical_target(target: str, profile: str, service_name: str | None = None) -> str:
    normalized_target = target.strip()
    if not normalized_target:
        return normalized_target
    if normalized_target.startswith(("pod/", "deployment/", "service/", "node/")):
        return normalized_target
    scope = scope_from_target(normalized_target, profile)
    if scope == "service":
        return f"service/{service_name or normalized_target}"
    if scope == "node":
        return f"node/{normalized_target}"
    return normalized_target
