from investigation_service.models import BuildInvestigationPlanRequest

from .evidence_collectors import ExternalEvidenceCollector
from .orchestrator import run_alert_canary


def run_alert_canary_markdown(
    incident: BuildInvestigationPlanRequest,
    *,
    collector: ExternalEvidenceCollector,
) -> str:
    return run_alert_canary(incident, collector=collector).markdown
