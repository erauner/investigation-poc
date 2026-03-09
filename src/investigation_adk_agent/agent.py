import os
from dataclasses import dataclass

from investigation_service.models import BuildInvestigationPlanRequest

from .alert_canary import run_alert_canary_markdown
from .evidence_collectors import ExternalEvidenceCollector, InternalSurrogateWorkloadCollector
from .prompt import SYSTEM_INSTRUCTIONS


@dataclass(slots=True)
class InvestigationAgentCard:
    name: str
    description: str
    version: str = ""


DEFAULT_AGENT_CARD = InvestigationAgentCard(
    name="incident_triage_adk",
    description="Canary BYO investigation agent that runs the alert handoff loop in code.",
)

DEFAULT_MODEL = "openai/gpt-4.1-mini"


def run_investigation(
    *,
    alertname: str,
    namespace: str,
    target: str,
    cluster: str = "current-context",
    profile: str = "workload",
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    collector: ExternalEvidenceCollector | None = None,
) -> str:
    incident = BuildInvestigationPlanRequest(
        cluster=cluster,
        namespace=namespace,
        target=target,
        profile=profile,
        alertname=alertname,
        labels=labels or {},
        annotations=annotations or {},
    )
    return run_alert_canary_markdown(
        incident,
        collector=collector or InternalSurrogateWorkloadCollector(),
    )


def configured_model_name() -> str:
    return os.getenv("INVESTIGATION_ADK_MODEL", DEFAULT_MODEL)
