import pytest

from investigation_adk_agent import agent
from investigation_adk_agent import main


def test_run_investigation_delegates_to_canary(monkeypatch) -> None:
    captured = {}

    def fake_run_alert_canary_markdown(incident, *, collector):
        captured["incident"] = incident
        captured["collector"] = collector
        return "## Diagnosis\nExample"

    monkeypatch.setattr(agent, "run_alert_canary_markdown", fake_run_alert_canary_markdown)

    result = agent.run_investigation(
        alertname="PodCrashLooping",
        namespace="operator-smoke",
        target="pod/crashy",
        labels={"namespace": "operator-smoke", "pod": "crashy"},
    )

    assert result == "## Diagnosis\nExample"
    assert captured["incident"].alertname == "PodCrashLooping"
    assert captured["incident"].target == "pod/crashy"
    assert captured["collector"] is not None


def test_create_app_returns_kagent_app_when_adk_installed(monkeypatch) -> None:
    monkeypatch.setenv("KAGENT_URL", "http://127.0.0.1:8083")
    app = main.create_app()
    assert type(app).__name__ == "KAgentApp"
