from .models import (
    BuildInvestigationPlanRequest,
    CorrelatedChangesResponse,
    EvidenceBatchExecution,
    EvidenceBundle,
    ExecutedStepTrace,
    InvestigationFocusProvenance,
    InvestigationPlan,
    InvestigationState,
    InvestigationTarget,
    NormalizedInvestigationRequest,
    PlannerSeedExecutionFocus,
    StepArtifact,
    ToolPathTrace,
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
        subject_context=target.subject_context.model_copy(deep=True) if target.subject_context else None,
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


def _execution_focus_from_target(target: InvestigationTarget | None) -> PlannerSeedExecutionFocus | None:
    if target is None:
        return None
    return PlannerSeedExecutionFocus(
        scope=target.scope,
        target=target.target,
        profile=target.profile,
        node_name=target.node_name,
        service_name=target.service_name,
    )


def _step_provenance(executions: list[EvidenceBatchExecution]) -> list[ExecutedStepTrace]:
    traces: list[ExecutedStepTrace] = []
    for execution in executions:
        for artifact in execution.artifacts:
            if artifact.route_provenance is None:
                continue
            traces.append(
                ExecutedStepTrace(
                    batch_id=execution.batch_id,
                    step_id=artifact.step_id,
                    plane=artifact.plane,
                    artifact_type=artifact.artifact_type,
                    provenance=artifact.route_provenance,
                )
            )
    return traces


def _focus_provenance_for_state(
    plan: InvestigationPlan,
    aligned_target: InvestigationTarget | None,
) -> InvestigationFocusProvenance | None:
    focus_provenance = plan.focus_provenance
    if focus_provenance is None and aligned_target is None:
        return None
    if focus_provenance is None:
        subject_context = aligned_target.subject_context if aligned_target is not None else None
        current_focus = _execution_focus_from_target(aligned_target)
        return InvestigationFocusProvenance(
            requested_subject=aligned_target.requested_target if aligned_target is not None else None,
            soft_primary_focus=subject_context.primary_subject.model_copy(deep=True) if subject_context and subject_context.primary_subject else None,
            related_subjects_considered=[
                subject.model_copy(deep=True) for subject in (subject_context.related_subjects if subject_context else [])
            ],
            initial_bounded_execution_focus=current_focus,
            current_bounded_execution_focus=current_focus,
            initial_focus_reasons=list(aligned_target.normalization_notes) if aligned_target is not None else [],
        )

    updated = focus_provenance.model_copy(deep=True)
    current_focus = _execution_focus_from_target(aligned_target)
    if current_focus is not None:
        updated = updated.model_copy(update={"current_bounded_execution_focus": current_focus})
    return updated


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
        if aligned.source == "alert":
            notes.append(f"alert-derived pod target resolved to pod/{evidence_ref.name}")
        else:
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
    focus_provenance = _focus_provenance_for_state(plan, aligned_target)
    if focus_provenance is not None:
        plan = plan.model_copy(update={"focus_provenance": focus_provenance})
    tool_path_trace = ToolPathTrace(
        planner_path_used=bool(executions),
        mode=plan.mode,
        executed_batch_ids=[execution.batch_id for execution in executions],
        executed_step_ids=[
            step_id
            for execution in executions
            for step_id in execution.executed_step_ids
        ],
        step_provenance=_step_provenance(executions),
    )
    return InvestigationState(
        incident=incident,
        target=aligned_target,
        plan=plan,
        executions=list(executions),
        artifacts=artifacts,
        primary_evidence=primary_evidence,
        change_candidates=change_candidates,
        focus_provenance=focus_provenance,
        tool_path_trace=tool_path_trace,
    )
