from pathlib import Path

import yaml

from .models import GuidelineRule, InvestigationReport, ResolvedGuideline
from .settings import get_cluster_name, get_guidelines_enabled, get_guidelines_path

_CATEGORY_WEIGHT = {
    "next_step": 50,
    "data_source": 40,
    "delegation": 30,
    "safety": 20,
    "interpretation": 10,
}

_MATCH_WEIGHT = {
    "target_name": 70,
    "service_name": 60,
    "diagnosis": 55,
    "alertname": 50,
    "namespace": 40,
    "target_kind": 35,
    "confidence": 30,
    "scope": 20,
    "cluster": 10,
}


def _match_value(expected: str | None, actual: str | None) -> bool:
    if expected is None:
        return True
    if actual is None:
        return False
    return expected.strip().lower() == actual.strip().lower()


def load_guideline_rules() -> tuple[list[GuidelineRule], list[str]]:
    if not get_guidelines_enabled():
        return [], []

    path = Path(get_guidelines_path())
    if not path.exists():
        return [], [f"guideline registry unavailable: {path} not found"]

    try:
        payload = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [], [f"guideline registry unavailable: {exc}"]

    raw_rules = payload.get("guidelines", [])
    if not isinstance(raw_rules, list):
        return [], ["guideline registry unavailable: guidelines must be a list"]

    try:
        return [GuidelineRule.model_validate(item) for item in raw_rules], []
    except Exception as exc:
        return [], [f"guideline registry unavailable: {exc}"]


def resolve_guidelines(
    rules: list[GuidelineRule],
    report: InvestigationReport,
    *,
    alertname: str | None = None,
    namespace: str | None = None,
    service_name: str | None = None,
) -> list[ResolvedGuideline]:
    target_kind, _, target_name = report.target.partition("/")
    actual_values = {
        "scope": report.scope,
        "alertname": alertname,
        "namespace": namespace,
        "service_name": service_name,
        "target_kind": target_kind or None,
        "target_name": target_name or None,
        "diagnosis": report.diagnosis,
        "cluster": get_cluster_name(),
        "confidence": report.confidence,
    }

    resolved: list[ResolvedGuideline] = []
    for rule in rules:
        matched_on: list[str] = []
        attempted_match = False
        for field, actual in actual_values.items():
            expected = getattr(rule.match, field)
            if expected is not None:
                attempted_match = True
            if not _match_value(expected, actual):
                matched_on = []
                break
            if expected is not None:
                matched_on.append(field)
        if attempted_match and not matched_on:
            continue

        specificity = sum(_MATCH_WEIGHT.get(field, 0) for field in matched_on)
        for action in rule.actions:
            resolved.append(
                ResolvedGuideline(
                    id=rule.id,
                    category=action.category,
                    text=action.text,
                    matched_on=matched_on,
                    priority=(rule.priority * 100) + specificity + _CATEGORY_WEIGHT.get(action.category, 0),
                    source=action.source,
                    agent=action.agent,
                )
            )

    resolved.sort(key=lambda item: (-item.priority, item.id, item.category, item.text))
    return resolved
