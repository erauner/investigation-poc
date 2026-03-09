from dataclasses import dataclass, field
from typing import Protocol

from investigation_service.models import (
    ActualRoute,
    CorrelatedChangesResponse,
    EvidenceBundle,
    EvidenceStepContract,
)


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
