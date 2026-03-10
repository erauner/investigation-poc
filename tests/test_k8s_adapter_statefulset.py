from investigation_service.k8s_adapter import get_related_events, resolve_target, resolve_runtime_target
from investigation_service.models import TargetRef


def test_resolve_target_accepts_statefulset_prefix() -> None:
    target = resolve_target("operator-smoke", "statefulset/postgres")
    assert target == TargetRef(namespace="operator-smoke", kind="statefulset", name="postgres")


def test_resolve_runtime_target_preserves_statefulset_without_cluster_lookup() -> None:
    target = TargetRef(namespace="operator-smoke", kind="statefulset", name="postgres")
    assert resolve_runtime_target(target) == target


def test_get_related_events_uses_statefulset_kind_casing(monkeypatch) -> None:
    seen_args: list[list[str]] = []

    def _fake_run_kubectl(args: list[str], cluster=None):
        seen_args.append(args)
        return True, "Warning FailedCreate example"

    monkeypatch.setattr("investigation_service.k8s_adapter._run_kubectl", _fake_run_kubectl)
    monkeypatch.setattr(
        "investigation_service.k8s_adapter._first_pod_for_workload",
        lambda namespace, workload_kind, workload_name, cluster=None: None,
    )

    events = get_related_events(TargetRef(namespace="operator-smoke", kind="statefulset", name="postgres"))

    assert events == ["Warning FailedCreate example"]
    assert any("involvedObject.kind=StatefulSet" in ",".join(args) for args in seen_args)
