from investigation_service.k8s_adapter import get_related_events, resolve_target
from investigation_service.models import TargetRef


def test_resolve_target_prefers_service_for_ambiguous_bare_name(monkeypatch) -> None:
    existing = {
        ("observability", "pod", "giraffe-kube-prometheus-st-prometheus"): False,
        ("observability", "deployment", "giraffe-kube-prometheus-st-prometheus"): False,
        ("observability", "service", "giraffe-kube-prometheus-st-prometheus"): True,
    }
    monkeypatch.setattr(
        "investigation_service.k8s_adapter._resource_exists",
        lambda namespace, kind, name: existing.get((namespace, kind, name), False),
    )

    resolved = resolve_target("observability", "giraffe-kube-prometheus-st-prometheus")

    assert resolved == TargetRef(
        namespace="observability",
        kind="service",
        name="giraffe-kube-prometheus-st-prometheus",
    )


def test_get_related_events_filters_service_events_by_kind(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run_kubectl(args: list[str]) -> tuple[bool, str]:
        calls.append(args)
        return True, "Normal Updated service changed\n"

    monkeypatch.setattr("investigation_service.k8s_adapter._run_kubectl", fake_run_kubectl)

    events = get_related_events(TargetRef(namespace="observability", kind="service", name="api"))

    assert events == ["Normal Updated service changed"]
    assert any("involvedObject.name=api,involvedObject.kind=Service" in arg for call in calls for arg in call)
