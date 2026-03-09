from dataclasses import dataclass, field
from typing import Protocol

from investigation_service.models import CollectContextRequest
from investigation_service.models import (
    ActualRoute,
    CorrelatedChangesResponse,
    EvidenceBundle,
    EvidenceStepContract,
)
from investigation_service.tools import collect_workload_evidence


@dataclass(slots=True)
class CollectedExternalStep:
    step_id: str
    actual_route: ActualRoute
    evidence_bundle: EvidenceBundle | None = None
    change_candidates: CorrelatedChangesResponse | None = None
    summary: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


class ExternalEvidenceCollector(Protocol):
    def collect_for_step(self, step: EvidenceStepContract) -> CollectedExternalStep:
        ...


class InternalSurrogateWorkloadCollector:
    """Temporary local smoke collector that reuses current internal workload evidence helpers."""

    def collect_for_step(self, step: EvidenceStepContract) -> CollectedExternalStep:
        inputs = step.execution_inputs
        if inputs.request_kind != "target_context":
            raise ValueError(f"unsupported request_kind {inputs.request_kind} for surrogate collector")
        if not inputs.namespace or not inputs.target or not inputs.profile:
            raise ValueError("target_context inputs must include namespace, target, and profile")
        bundle = collect_workload_evidence(
            CollectContextRequest(
                cluster=inputs.cluster,
                namespace=inputs.namespace,
                target=inputs.target,
                profile=inputs.profile,
                service_name=inputs.service_name,
                lookback_minutes=inputs.lookback_minutes or 15,
            )
        )
        return CollectedExternalStep(
            step_id=step.step_id,
            actual_route=ActualRoute(
                source_kind="investigation_internal",
                mcp_server="investigation-mcp-server",
                tool_name="collect_workload_evidence",
                tool_path=["investigation_adk_agent.InternalSurrogateWorkloadCollector", "collect_workload_evidence"],
            ),
            evidence_bundle=bundle,
            summary=["Collected workload evidence via internal surrogate collector for local canary smoke."],
            limitations=list(bundle.limitations),
        )
