from .models import (
    ActualRoute,
    CorrelatedChangesResponse,
    EvidenceBundle,
    EvidenceStepContract,
    SubmittedStepArtifact,
)


def materialize_submitted_step(
    step: EvidenceStepContract,
    *,
    actual_route: ActualRoute,
    evidence_bundle: EvidenceBundle | None = None,
    change_candidates: CorrelatedChangesResponse | None = None,
    summary: list[str] | None = None,
    limitations: list[str] | None = None,
) -> SubmittedStepArtifact:
    if step.execution_mode != "external_preferred":
        raise ValueError(f"step {step.step_id} is not externally satisfiable")

    if step.artifact_type == "evidence_bundle":
        if evidence_bundle is None:
            raise ValueError(f"step {step.step_id} requires evidence_bundle payload")
        if change_candidates is not None:
            raise ValueError(f"step {step.step_id} does not accept change_candidates payload")
        return SubmittedStepArtifact(
            step_id=step.step_id,
            evidence_bundle=evidence_bundle,
            actual_route=actual_route,
            summary=list(summary or []),
            limitations=list(limitations or []),
        )

    if change_candidates is None:
        raise ValueError(f"step {step.step_id} requires change_candidates payload")
    if evidence_bundle is not None:
        raise ValueError(f"step {step.step_id} does not accept evidence_bundle payload")
    return SubmittedStepArtifact(
        step_id=step.step_id,
        change_candidates=change_candidates,
        actual_route=actual_route,
        summary=list(summary or []),
        limitations=list(limitations or []),
    )
