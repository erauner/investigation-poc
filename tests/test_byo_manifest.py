from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: str) -> dict:
    with (ROOT / path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_optional_byo_adk_agent_is_parallel_canary() -> None:
    manifest = _load_yaml("k8s/optional-byo-adk/agent.yaml")

    assert manifest["kind"] == "Agent"
    assert manifest["metadata"]["name"] == "incident-triage-adk"
    assert manifest["spec"]["type"] == "BYO"
    assert "Canary" in manifest["spec"]["description"]
    env = manifest["spec"]["byo"]["deployment"]["env"]
    assert any(item["name"] == "KAGENT_URL" for item in env)


def test_optional_byo_adk_agent_not_in_default_kustomization() -> None:
    kustomization = _load_yaml("k8s/kustomization.yaml")
    resources = set(kustomization.get("resources", []))

    assert "optional-byo-adk/agent.yaml" not in resources
