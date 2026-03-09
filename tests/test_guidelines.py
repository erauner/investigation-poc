from investigation_service.guidelines import (
    guideline_context_from_analysis,
    load_guideline_rules,
    resolve_guidelines,
    resolve_guidelines_for_context,
)
from investigation_service.models import (
    EvidenceItem,
    GuidelineContext,
    GuidelineRule,
    Hypothesis,
    InvestigationAnalysis,
    InvestigationTarget,
    InvestigationReport,
    InvestigationReportRequest,
    RootCauseReport,
)
from investigation_service import reporting


def test_load_guideline_rules_from_yaml(tmp_path, monkeypatch) -> None:
    path = tmp_path / "guidelines.yaml"
    path.write_text(
        """
guidelines:
  - id: service-envoy
    priority: 250
    match:
      scope: service
      alertname: EnvoyHighErrorRate
      namespace: kagent
    actions:
      - category: next_step
        text: Inspect recent rollout and upstream dependency health before reading pod logs.
      - category: data_source
        text: Prefer service dashboards and trace views before kubectl logs.
"""
    )
    monkeypatch.setenv("GUIDELINES_PATH", str(path))
    monkeypatch.setenv("GUIDELINES_ENABLED", "true")

    rules, limitations = load_guideline_rules()

    assert limitations == []
    assert len(rules) == 1
    assert rules[0].id == "service-envoy"
    assert rules[0].actions[0].category == "next_step"


def test_resolve_guidelines_prefers_more_specific_rule() -> None:
    report = InvestigationReport(
        scope="service",
        target="service/kagent-controller",
        diagnosis="Service Returning 5xx Responses",
        likely_cause=None,
        confidence="medium",
        evidence=["prometheus: 5xx spike"],
        evidence_items=[],
        related_data=[],
        related_data_note=None,
        limitations=[],
        recommended_next_step="base",
        suggested_follow_ups=[],
        guidelines=[],
        normalization_notes=[],
    )
    specific = GuidelineRule.model_validate(
        {
            "id": "specific",
            "priority": 100,
            "match": {
                "scope": "service",
                "namespace": "kagent",
                "service_name": "kagent-controller",
                "alertname": "EnvoyHighErrorRate",
            },
            "actions": [{"category": "next_step", "text": "Use the specific path first."}],
        }
    )
    global_rule = GuidelineRule.model_validate(
        {
            "id": "global",
            "priority": 100,
            "match": {"scope": "service"},
            "actions": [{"category": "next_step", "text": "Use the global path."}],
        }
    )

    resolved = resolve_guidelines(
        [global_rule, specific],
        report,
        alertname="EnvoyHighErrorRate",
        namespace="kagent",
        service_name="kagent-controller",
    )

    assert [item.id for item in resolved] == ["specific", "global"]


def test_resolve_guidelines_for_context_matches_legacy_resolution() -> None:
    report = InvestigationReport(
        scope="service",
        target="service/kagent-controller",
        diagnosis="Service Returning 5xx Responses",
        likely_cause=None,
        confidence="medium",
        evidence=["prometheus: 5xx spike"],
        evidence_items=[],
        related_data=[],
        related_data_note=None,
        limitations=[],
        recommended_next_step="base",
        suggested_follow_ups=[],
        guidelines=[],
        normalization_notes=[],
    )
    analysis = InvestigationAnalysis(
        cluster="erauner-home",
        scope="service",
        target="service/kagent-controller",
        profile="service",
        hypotheses=[
            Hypothesis(
                key="service-5xx",
                diagnosis="Service Returning 5xx Responses",
                likely_cause=None,
                confidence="medium",
                score=1,
                supporting_findings=[],
                evidence_items=[],
            )
        ],
        limitations=[],
        recommended_next_step="base",
        suggested_follow_ups=[],
    )
    target = InvestigationTarget(
        source="manual",
        scope="service",
        cluster="erauner-home",
        namespace="kagent",
        requested_target="service/kagent-controller",
        target="service/kagent-controller",
        node_name=None,
        service_name="kagent-controller",
        profile="service",
        lookback_minutes=15,
        normalization_notes=[],
    )
    specific = GuidelineRule.model_validate(
        {
            "id": "specific",
            "priority": 100,
            "match": {
                "scope": "service",
                "namespace": "kagent",
                "service_name": "kagent-controller",
                "alertname": "EnvoyHighErrorRate",
            },
            "actions": [{"category": "next_step", "text": "Use the specific path first."}],
        }
    )
    global_rule = GuidelineRule.model_validate(
        {
            "id": "global",
            "priority": 100,
            "match": {"scope": "service"},
            "actions": [{"category": "next_step", "text": "Use the global path."}],
        }
    )

    legacy = resolve_guidelines(
        [global_rule, specific],
        report,
        alertname="EnvoyHighErrorRate",
        namespace="kagent",
        service_name="kagent-controller",
    )
    context = guideline_context_from_analysis(analysis, target, alertname="EnvoyHighErrorRate")
    artifact = resolve_guidelines_for_context([global_rule, specific], context)

    assert artifact == legacy


def test_build_investigation_report_applies_guidelines_without_mutating_diagnosis(monkeypatch) -> None:
    root_cause = RootCauseReport(
        scope="service",
        target="service/kagent-controller",
        diagnosis="Service Returning 5xx Responses",
        likely_cause="Backend dependency is failing under live traffic.",
        confidence="medium",
        evidence=["prometheus: Service Returning 5xx Responses - 5xx ratio above threshold"],
        evidence_items=[
            EvidenceItem(
                fingerprint="finding|service|5xx",
                source="prometheus",
                kind="finding",
                severity="critical",
                summary="prometheus: Service Returning 5xx Responses",
                detail="5xx ratio above threshold",
            )
        ],
        limitations=["metrics partial"],
        recommended_next_step="Inspect service dashboards, recent deploys, and upstream or downstream dependencies before changing traffic handling.",
        suggested_follow_ups=["Check whether a recent rollout lines up with the degradation."],
    )
    rules = [
        GuidelineRule.model_validate(
            {
                "id": "envoy-service",
                "priority": 300,
                "match": {
                    "scope": "service",
                    "namespace": "kagent",
                    "service_name": "kagent-controller",
                    "diagnosis": "Service Returning 5xx Responses",
                },
                "actions": [
                    {
                        "category": "next_step",
                        "text": "Inspect recent rollout and upstream dependency health before reading workload logs.",
                    },
                    {
                        "category": "data_source",
                        "text": "Prefer service dashboards and traces before kubectl logs for this service.",
                    },
                ],
            }
        )
    ]

    monkeypatch.setattr(
        reporting,
        "execute_investigation_step",
        lambda _req: reporting.EvidenceBatchExecution(
            batch_id="batch-1",
            executed_step_ids=["collect-target-evidence"],
            artifacts=[
                {
                    "step_id": "collect-target-evidence",
                    "plane": "service",
                    "artifact_type": "evidence_bundle",
                    "summary": [],
                    "limitations": [],
                    "evidence_bundle": {
                        "cluster": "current-context",
                        "target": {"namespace": "kagent", "kind": "service", "name": "kagent-controller"},
                        "object_state": {"kind": "service", "name": "kagent-controller"},
                        "events": [],
                        "log_excerpt": "",
                        "metrics": {},
                        "findings": [],
                        "limitations": [],
                        "enrichment_hints": [],
                    },
                }
            ],
            execution_notes=[],
        ),
    )
    monkeypatch.setattr(
        reporting,
        "update_investigation_plan",
        lambda req: req.plan.model_copy(update={"active_batch_id": None}),
    )
    monkeypatch.setattr(reporting, "build_root_cause_report_impl", lambda context, normalized: root_cause)
    monkeypatch.setattr(reporting, "load_guideline_rules", lambda: (rules, []))

    report = reporting.build_investigation_report(
        InvestigationReportRequest(
            namespace="kagent",
            target="service/kagent-controller",
            profile="service",
            include_related_data=False,
        )
    )

    assert report.diagnosis == root_cause.diagnosis
    assert report.evidence == root_cause.evidence
    assert report.recommended_next_step == (
        "Inspect recent rollout and upstream dependency health before reading workload logs."
    )
    assert report.suggested_follow_ups == [
        "Check whether a recent rollout lines up with the degradation.",
        "Prefer service dashboards and traces before kubectl logs for this service.",
    ]
    assert [item.category for item in report.guidelines] == ["next_step", "data_source"]
