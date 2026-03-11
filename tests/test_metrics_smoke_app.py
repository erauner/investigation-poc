from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types


APP_PATH = Path(__file__).resolve().parents[1] / "testapps/metrics_smoke/app.py"


def _install_fake_prometheus_client() -> None:
    module = types.ModuleType("prometheus_client")

    class CollectorRegistry:
        def __init__(self):
            self.has_metrics = False

    class _HistogramObserver:
        def __init__(self, registry):
            self._registry = registry

        def observe(self, _value):
            self._registry.has_metrics = True

    class Histogram:
        def __init__(self, _name, _description, _labels, registry):
            self._registry = registry

        def labels(self, **_kwargs):
            return _HistogramObserver(self._registry)

    def generate_latest(registry=None):
        if registry is not None and getattr(registry, "has_metrics", False):
            return b"http_server_request_duration_seconds_bucket 1\n"
        return b""

    module.CONTENT_TYPE_LATEST = "text/plain"
    module.CollectorRegistry = CollectorRegistry
    module.Histogram = Histogram
    module.generate_latest = generate_latest
    sys.modules["prometheus_client"] = module


def _load_metrics_smoke_app():
    _install_fake_prometheus_client()
    spec = spec_from_file_location("metrics_smoke_app", APP_PATH)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parse_metrics_scenario_defaults_to_healthy_complete() -> None:
    module = _load_metrics_smoke_app()

    assert module.parse_metrics_scenario(None) == module.MetricsScenario.HEALTHY_COMPLETE
    assert module.parse_metrics_scenario("weak_but_usable") == module.MetricsScenario.WEAK_BUT_USABLE
    assert module.parse_metrics_scenario("loki_complementary") == module.MetricsScenario.LOKI_COMPLEMENTARY


def test_metrics_registry_for_weak_scenario_hides_metrics_after_cutover(monkeypatch) -> None:
    module = _load_metrics_smoke_app()
    monkeypatch.setattr(module, "APP_START_MONOTONIC", 100.0)
    monkeypatch.setattr(module, "HIDE_AFTER_SECONDS", 45)

    before_cutover = module.metrics_registry_for_scenario(
        module.MetricsScenario.WEAK_BUT_USABLE,
        now_monotonic=120.0,
    )
    after_cutover = module.metrics_registry_for_scenario(
        module.MetricsScenario.WEAK_BUT_USABLE,
        now_monotonic=200.0,
    )

    assert before_cutover is module.SERVICE_METRICS_REGISTRY
    assert after_cutover is module.EMPTY_METRICS_REGISTRY


def test_empty_or_broken_registry_does_not_emit_service_histogram() -> None:
    module = _load_metrics_smoke_app()
    module.REQUEST_LATENCY.labels(method="GET", route="/ok", status="200").observe(0.1)

    healthy_output = module.generate_latest(
        module.metrics_registry_for_scenario(module.MetricsScenario.HEALTHY_COMPLETE)
    ).decode("utf-8")
    empty_output = module.generate_latest(
        module.metrics_registry_for_scenario(module.MetricsScenario.EMPTY_OR_BROKEN)
    ).decode("utf-8")

    assert "http_server_request_duration_seconds" in healthy_output
    assert "http_server_request_duration_seconds" not in empty_output


def test_loki_complementary_registry_emits_service_histogram() -> None:
    module = _load_metrics_smoke_app()
    module.REQUEST_LATENCY.labels(method="GET", route="/slow", status="200").observe(1.6)

    output = module.generate_latest(
        module.metrics_registry_for_scenario(module.MetricsScenario.LOKI_COMPLEMENTARY)
    ).decode("utf-8")

    assert "http_server_request_duration_seconds" in output


def test_build_loki_push_payload_includes_namespace_pod_and_service(monkeypatch) -> None:
    module = _load_metrics_smoke_app()
    monkeypatch.setattr(module, "POD_NAMESPACE", "metrics-smoke")
    monkeypatch.setattr(module, "POD_NAME", "metrics-api-abc123")
    monkeypatch.setattr(module, "SERVICE_NAME", "metrics-api")

    payload = module.build_loki_push_payload("error: upstream returned 500", timestamp_ns=123)

    assert payload == (
        b'{"streams": [{"stream": {"job": "metrics-smoke", "namespace": "metrics-smoke", '
        b'"pod": "metrics-api-abc123", "service": "metrics-api"}, "values": [["123", '
        b'"error: upstream returned 500"]]}]}'
    )
