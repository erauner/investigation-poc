from dataclasses import dataclass, field

from investigation_adk_agent.evidence_collectors import CollectedExternalStep
from investigation_adk_agent.orchestrator import run_alert_canary
from investigation_service.models import (
    ActualRoute,
    BuildInvestigationPlanRequest,
    EvidenceBatchExecution,
    EvidenceBundle,
    EvidenceStepContract,
    Finding,
    HandoffActiveEvidenceBatchResponse,
    InvestigationReport,
    ReportingExecutionContext,
    StepExecutionInputs,
    TargetRef,
)


def _incident() -> BuildInvestigationPlanRequest:
    return BuildInvestigationPlanRequest(
        cluster="kind-investigation",
        namespace="operator-smoke",
        target="pod/crashy",
        profile="workload",
        alertname="PodCrashLooping",
        labels={"namespace": "operator-smoke", "pod": "crashy"},
        annotations={"summary": "Crash loop detected"},
    )


def _bundle() -> EvidenceBundle:
    return EvidenceBundle(
        cluster="kind-investigation",
        target=TargetRef(namespace="operator-smoke", kind="pod", name="crashy-abc123"),
        object_state={"kind": "Pod", "name": "crashy-abc123"},
        events=[],
        log_excerpt="CrashLoopBackOff",
        metrics={},
        findings=[
                Finding(
                    severity="critical",
                    source="k8s",
                    title="Crash loop detected",
                    evidence="Pod restarted repeatedly",
                )
        ],
        limitations=[],
        enrichment_hints=[],
    )


def _context() -> ReportingExecutionContext:
    return ReportingExecutionContext(
        initial_plan=None,
        updated_plan={
            "mode": "alert_rca",
            "objective": "Investigate PodCrashLooping",
            "target": None,
            "steps": [],
            "evidence_batches": [],
            "active_batch_id": None,
            "planning_notes": [],
        },
        executions=[],
        allow_bounded_fallback_execution=False,
    )


def _step() -> EvidenceStepContract:
    return EvidenceStepContract(
        step_id="collect-target-evidence",
        title="Collect target evidence",
        plane="workload",
        artifact_type="evidence_bundle",
        requested_capability="workload_evidence_plane",
        preferred_mcp_server="kubernetes-mcp-server",
        preferred_tool_names=["pods_log", "resources_get"],
        fallback_mcp_server="investigation-mcp-server",
        fallback_tool_names=["collect_workload_evidence"],
        execution_mode="external_preferred",
        execution_inputs=StepExecutionInputs(
            request_kind="target_context",
            cluster="kind-investigation",
            namespace="operator-smoke",
            target="deployment/crashy",
            profile="workload",
        ),
    )


@dataclass
class FakeCollector:
    called_step_ids: list[str] = field(default_factory=list)

    def collect_for_step(self, step: EvidenceStepContract) -> CollectedExternalStep:
        self.called_step_ids.append(step.step_id)
        return CollectedExternalStep(
            step_id=step.step_id,
            actual_route=ActualRoute(
                source_kind="peer_mcp",
                mcp_server="kubernetes-mcp-server",
                tool_name="pods_log",
                tool_path=["pods_log", "resources_get"],
            ),
            evidence_bundle=_bundle(),
            summary=["Collected crash-loop evidence from the workload peer plane."],
        )


def test_run_alert_canary_submits_external_steps_and_renders(monkeypatch) -> None:
    first = HandoffActiveEvidenceBatchResponse(
        execution_context=_context(),
        handoff_token="token-1",
        active_batch={
            "batch_id": "batch-1",
                "title": "Initial alert evidence",
                "intent": "Collect alert evidence",
                "subject": {
                    "source": "alert",
                    "kind": "alert",
                    "summary": "Investigate PodCrashLooping for pod/crashy.",
                    "requested_target": "pod/crashy",
                    "alertname": "PodCrashLooping",
                },
                "canonical_target": None,
                "steps": [_step().model_dump(mode="python")],
            },
        execution=None,
        handoff_status="awaiting_external_submission",
        next_action="submit_external_steps",
        required_external_step_ids=["collect-target-evidence"],
    )
    second = HandoffActiveEvidenceBatchResponse(
        execution_context=_context(),
        handoff_token="token-2",
        active_batch=None,
        execution=EvidenceBatchExecution(
            batch_id="batch-1",
            executed_step_ids=["collect-target-evidence", "collect-alert-evidence", "collect-change-candidates"],
            artifacts=[],
            execution_notes=["advanced bounded evidence batch batch-1"],
        ),
        handoff_status="complete",
        next_action="render_report",
        required_external_step_ids=[],
    )
    responses = [first, second]
    seen_requests = []

    def fake_handoff(req):
        seen_requests.append(req)
        return responses.pop(0)

    rendered_requests = []

    def fake_render(req):
        rendered_requests.append(req)
        return InvestigationReport(
            cluster="kind-investigation",
            scope="workload",
            target="pod/crashy-abc123",
            diagnosis="CrashLoopBackOff on the resolved pod.",
            confidence="high",
            evidence=["Pod restarted repeatedly and logs show startup failure."],
            related_data=[],
            related_data_note="No meaningful correlated changes found in the requested time window.",
            limitations=["No broader peer evidence was collected in the canary test."],
            recommended_next_step="Inspect the failing container startup configuration.",
        )

    monkeypatch.setattr("investigation_service.reporting.handoff_active_evidence_batch", fake_handoff)
    monkeypatch.setattr("investigation_service.reporting.render_investigation_report", fake_render)

    collector = FakeCollector()
    result = run_alert_canary(_incident(), collector=collector)

    assert collector.called_step_ids == ["collect-target-evidence"]
    assert seen_requests[0].handoff_token is None
    assert seen_requests[1].handoff_token == "token-1"
    assert len(seen_requests[1].submitted_steps) == 1
    assert seen_requests[1].submitted_steps[0].step_id == "collect-target-evidence"
    assert rendered_requests[0].execution_context == second.execution_context
    assert "## Diagnosis" in result.markdown
    assert "## Recommended next step" in result.markdown
    assert "CrashLoopBackOff on the resolved pod." in result.markdown
