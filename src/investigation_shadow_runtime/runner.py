from dataclasses import dataclass

from investigation_orchestrator import OrchestratorRuntimeConfig, run_orchestrated_investigation_runtime
from investigation_orchestrator.entrypoint import _maybe_attach_resolved_pod_context

from .host_adapter import format_shadow_report, parse_shadow_task


@dataclass(frozen=True)
class ShadowInvestigationResult:
    markdown: str
    runtime_status: str


def run_shadow_investigation(
    task: str,
    *,
    runtime: OrchestratorRuntimeConfig | None = None,
) -> ShadowInvestigationResult:
    request = parse_shadow_task(task)
    result = run_orchestrated_investigation_runtime(
        request,
        runtime=runtime,
    )
    if result.final_report is None:
        next_nodes = ", ".join(result.next_nodes) or "unknown"
        raise ValueError(f"shadow investigation interrupted before completion; next_nodes={next_nodes}")
    report = _maybe_attach_resolved_pod_context(request, result.final_report)
    return ShadowInvestigationResult(
        markdown=format_shadow_report(report),
        runtime_status=result.status,
    )
