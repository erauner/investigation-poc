from investigation_service.k8s_adapter import resolve_target, resolve_runtime_target
from investigation_service.models import TargetRef


def test_resolve_target_accepts_statefulset_prefix() -> None:
    target = resolve_target("operator-smoke", "statefulset/postgres")
    assert target == TargetRef(namespace="operator-smoke", kind="statefulset", name="postgres")


def test_resolve_runtime_target_preserves_statefulset_without_cluster_lookup() -> None:
    target = TargetRef(namespace="operator-smoke", kind="statefulset", name="postgres")
    assert resolve_runtime_target(target) == target
