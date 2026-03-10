from investigation_service.models import CorrelatedChange, EvidenceItem, InvestigationReport, ResolvedGuideline, ToolPathTrace
from investigation_service.presentation import render_presentation_document, render_presentation_markdown


def _report() -> InvestigationReport:
    return InvestigationReport(
        cluster="erauner-home",
        scope="workload",
        target="deployment/crashy",
        diagnosis="CrashLoopBackOff",
        likely_cause="Container exits immediately after startup",
        confidence="high",
        evidence=[
            "events: Crash Loop Detected - Events indicate BackOff/CrashLoopBackOff behavior",
            "logs: application exits with status 1",
        ],
        evidence_items=[
            EvidenceItem(
                fingerprint="finding|1",
                source="k8s",
                kind="finding",
                severity="critical",
                summary="k8s: Crash Loop Detected",
                detail="container=app, waiting reason=CrashLoopBackOff, restarts=5",
            ),
            EvidenceItem(
                fingerprint="event|1",
                source="events",
                kind="event",
                severity="warning",
                summary="recent events",
                detail="Warning BackOff Back-off restarting failed container crashy in pod crashy-abc123",
            ),
        ],
        related_data=[
            CorrelatedChange(
                fingerprint="change|1",
                timestamp="2026-03-10T12:00:00Z",
                source="rollout",
                resource_kind="Deployment",
                namespace="kagent-smoke",
                name="crashy",
                relation="same_workload",
                summary="Deployment rolled out shortly before failures.",
                confidence="medium",
            )
        ],
        related_data_note="No additional changes found.",
        limitations=["No trace data was available."],
        recommended_next_step="Inspect the failing container command.",
        suggested_follow_ups=["Compare the current image tag to the previous successful revision."],
        guidelines=[
            ResolvedGuideline(
                id="g1",
                category="next_step",
                text="Validate recent rollouts before taking write actions.",
                matched_on=["diagnosis"],
                priority=10,
            )
        ],
        normalization_notes=["target derived from alert text"],
        tool_path_trace=ToolPathTrace(
            planner_path_used=True,
            source="investigation-mcp-server",
            mode="targeted_rca",
            executed_batch_ids=["batch-1"],
            executed_step_ids=["collect-target-evidence"],
            step_provenance=[],
        ),
    )


def test_render_presentation_document_returns_requested_profile() -> None:
    document = render_presentation_document(_report(), profile="incident_report")

    assert document.profile == "incident_report"
    assert [section.title for section in document.sections] == [
        "Incident Summary",
        "Supporting Evidence",
        "Related Context",
        "Limitations",
        "Next Actions",
    ]


def test_operator_summary_preserves_shadow_shape_and_structured_evidence() -> None:
    rendered = render_presentation_markdown(_report(), profile="operator_summary")

    assert "## Diagnosis" in rendered
    assert "## Evidence" in rendered
    assert "## Related Data" in rendered
    assert "## Limitations" in rendered
    assert "## Recommended next step" in rendered
    assert "recent events: Back-off restarting failed container crashy in pod crashy-abc123" in rendered
    assert "k8s: Crash Loop Detected - container=app, waiting reason=CrashLoopBackOff, restarts=5" not in rendered


def test_debug_trace_includes_trace_notes_and_guidelines_without_changing_headline_semantics() -> None:
    rendered = render_presentation_markdown(_report(), profile="debug_trace")

    assert "CrashLoopBackOff" in rendered
    assert "Planner path used: True" in rendered
    assert "Executed steps: collect-target-evidence" in rendered
    assert "target derived from alert text" in rendered
    assert "[next_step] Validate recent rollouts before taking write actions." in rendered


def test_explain_more_surfaces_follow_ups_and_related_data_note() -> None:
    report = _report().model_copy(update={"related_data": []})
    rendered = render_presentation_markdown(report, profile="explain_more")

    assert "## Follow-ups And Guidance" in rendered
    assert "Inspect the failing container command." in rendered
    assert "Compare the current image tag to the previous successful revision." in rendered
    assert "No additional changes found." in rendered
