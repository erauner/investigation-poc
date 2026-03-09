from .models import (
    BuildInvestigationPlanRequest,
    CorrelatedChangesResponse,
    EvidenceBatchExecution,
    EvidenceBundle,
    InvestigationPlan,
    InvestigationState,
    InvestigationTarget,
    NormalizedInvestigationRequest,
    StepArtifact,
)


def normalized_request_from_target(target: InvestigationTarget) -> NormalizedInvestigationRequest:
    return NormalizedInvestigationRequest(
        source=target.source,
        scope=target.scope,
        cluster=target.cluster,
        namespace=target.namespace,
        target=target.target,
        node_name=target.node_name,
        service_name=target.service_name,
        profile=target.profile,
        lookback_minutes=target.lookback_minutes,
        normalization_notes=list(target.normalization_notes),
    )


def _primary_evidence_from_artifacts(artifacts: list[StepArtifact]) -> EvidenceBundle | None:
    artifact = next((item for item in artifacts if item.step_id == "collect-target-evidence"), None)
    if artifact is None:
        artifact = next((item for item in artifacts if item.evidence_bundle is not None), None)
    if artifact is None:
        return None
    return artifact.evidence_bundle


def _change_candidates_from_artifacts(artifacts: list[StepArtifact]) -> CorrelatedChangesResponse | None:
    artifact = next((item for item in artifacts if item.change_candidates is not None), None)
    if artifact is None:
        return None
    return artifact.change_candidates


def align_target_with_primary_evidence(
    target: InvestigationTarget | None,
    evidence: EvidenceBundle | None,
) -> InvestigationTarget | None:
    if target is None or evidence is None:
        return target

    aligned = target
    notes = list(target.normalization_notes)
    evidence_ref = evidence.target

    if evidence.cluster and not any(note.startswith("cluster resolved") for note in notes):
        notes.append(f"cluster resolved from collected context: {evidence.cluster}")
        aligned = aligned.model_copy(update={"cluster": evidence.cluster, "normalization_notes": notes})
        notes = list(aligned.normalization_notes)

    if evidence_ref.kind == "pod" and aligned.target.startswith("pod/") and aligned.target != f"pod/{evidence_ref.name}":
        notes.append(f"resolved pod target to {evidence_ref.name}")
        aligned = aligned.model_copy(update={"target": f"pod/{evidence_ref.name}", "normalization_notes": notes})
        notes = list(aligned.normalization_notes)

    if evidence_ref.kind == "node":
        target_value = f"node/{evidence_ref.name}"
        if aligned.target != target_value or aligned.node_name != evidence_ref.name:
            aligned = aligned.model_copy(update={"target": target_value, "node_name": evidence_ref.name})

    if evidence_ref.kind == "service" and aligned.scope != "service":
        notes.append("profile promoted to service after resolving target kind=service")
        aligned = aligned.model_copy(
            update={
                "scope": "service",
                "profile": "service",
                "target": f"service/{evidence_ref.name}",
                "service_name": aligned.service_name or evidence_ref.name,
                "normalization_notes": notes,
            }
        )

    return aligned


def build_investigation_state(
    *,
    incident: BuildInvestigationPlanRequest,
    initial_plan: InvestigationPlan,
    updated_plan: InvestigationPlan,
    executions: list[EvidenceBatchExecution],
) -> InvestigationState:
    artifacts = [artifact for execution in executions for artifact in execution.artifacts]
    primary_evidence = _primary_evidence_from_artifacts(artifacts)
    change_candidates = _change_candidates_from_artifacts(artifacts)
    aligned_target = align_target_with_primary_evidence(updated_plan.target or initial_plan.target, primary_evidence)
    plan = updated_plan.model_copy(update={"target": aligned_target})
    return InvestigationState(
        incident=incident,
        target=aligned_target,
        plan=plan,
        executions=list(executions),
        artifacts=artifacts,
        primary_evidence=primary_evidence,
        change_candidates=change_candidates,
    )
