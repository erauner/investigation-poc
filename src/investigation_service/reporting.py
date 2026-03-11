import base64
import hashlib
import json
import zlib

from .analysis import (
    adjusted_confidence_from_hypotheses,
    ambiguity_limitations_from_hypotheses,
    build_investigation_analysis,
    follow_ups_from_hypotheses,
    primary_hypothesis,
    rendered_evidence_from_hypothesis,
)
from .cluster_registry import resolve_cluster
from .correlation import collect_change_candidates, collect_correlated_changes_for_target
from .event_fingerprints import canonicalize_event_fingerprint
from .guidelines import guideline_context_from_analysis, load_guideline_rules, resolve_guidelines_for_context
from .k8s_adapter import get_backend_cr, get_cluster_cr, get_frontend_cr
from .models import (
    ActiveEvidenceBatchContract,
    AdvanceInvestigationRuntimeRequest,
    AdvanceInvestigationRuntimeResponse,
    BuildInvestigationPlanRequest,
    CorrelatedChange,
    EvidenceBatchExecution,
    ExecuteInvestigationStepRequest,
    GetActiveEvidenceBatchRequest,
    HandoffActiveEvidenceBatchRequest,
    HandoffActiveEvidenceBatchResponse,
    InvestigationAnalysis,
    InvestigationPlan,
    InvestigationReport,
    InvestigationReportingRequest,
    InvestigationReportRequest,
    InvestigationState,
    InvestigationTarget,
    ReportingExecutionContext,
    ResolvedGuideline,
    SubmitEvidenceArtifactsRequest,
    SubmittedEvidenceReconciliationResult,
    UpdateInvestigationPlanRequest,
)
from .planner import PlannerDeps
from .routing import canonical_target, scope_from_target
from .state import build_investigation_state as build_investigation_state_artifact
from .tools import (
    collect_alert_evidence,
    collect_node_evidence,
    collect_service_evidence,
    collect_workload_evidence,
    find_unhealthy_pod,
    normalize_alert_input,
)
from . import planner

_EMPTY_CORRELATION_LIMITATION = "no correlated changes found in the requested time window"
_EMPTY_RELATED_DATA_NOTE = "no meaningful correlated changes found in the requested time window"
_HANDOFF_TOKEN_VERSION = 1


def _is_empty_correlation_limitation(value: str) -> bool:
    normalized = value.strip().lower()
    return "correlated changes" in normalized and "requested time window" in normalized


def _planner_deps() -> PlannerDeps:
    return PlannerDeps(
        normalize_alert_input=normalize_alert_input,
        canonical_target=canonical_target,
        scope_from_target=scope_from_target,
        resolve_cluster=resolve_cluster,
        get_backend_cr=get_backend_cr,
        get_frontend_cr=get_frontend_cr,
        get_cluster_cr=get_cluster_cr,
        find_unhealthy_pod=find_unhealthy_pod,
        collect_alert_evidence=collect_alert_evidence,
        collect_node_evidence=collect_node_evidence,
        collect_service_evidence=collect_service_evidence,
        collect_workload_evidence=collect_workload_evidence,
        collect_change_candidates=collect_change_candidates,
    )


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _supplemental_evidence_from_state(state: InvestigationState) -> list[str]:
    supplemental: list[str] = []
    for artifact in state.artifacts:
        if artifact.step_id == "collect-alert-evidence":
            supplemental.extend(artifact.summary)
    return _dedupe_preserving_order(supplemental)


def _filter_related_data_from_evidence(primary_evidence_items, changes: list[CorrelatedChange]) -> tuple[list[CorrelatedChange], str | None]:
    def dedupe_key(value: str) -> str:
        canonical = canonicalize_event_fingerprint(value)
        parts = canonical.split("|")
        if len(parts) == 6 and parts[0] == "event":
            return "|".join([parts[0], parts[1], parts[3], parts[4], parts[5]])
        return canonical

    primary_fingerprints = {dedupe_key(item.fingerprint) for item in primary_evidence_items}
    filtered = [change for change in changes if dedupe_key(change.fingerprint) not in primary_fingerprints]
    omitted = len(changes) - len(filtered)
    if filtered:
        if omitted:
            return filtered, f"{omitted} correlated change omitted because it duplicated primary evidence"
        return filtered, None
    if changes:
        return [], "all correlated changes duplicated primary evidence"
    return [], _EMPTY_RELATED_DATA_NOTE


def _filter_related_data(report, changes: list[CorrelatedChange]) -> tuple[list[CorrelatedChange], str | None]:
    return _filter_related_data_from_evidence(report.evidence_items, changes)


def _report_request_to_plan_request(req: InvestigationReportRequest) -> BuildInvestigationPlanRequest:
    return BuildInvestigationPlanRequest(
        cluster=req.cluster,
        namespace=req.namespace,
        target=req.target,
        question=req.question,
        profile=req.profile,
        service_name=req.service_name,
        lookback_minutes=req.lookback_minutes,
        alertname=req.alertname,
        labels=req.labels,
        annotations=req.annotations,
        node_name=req.node_name,
        objective="auto",
    )


def build_investigation_plan(req: BuildInvestigationPlanRequest) -> InvestigationPlan:
    return planner.build_investigation_plan(req, _planner_deps())


def execute_investigation_step(req: ExecuteInvestigationStepRequest) -> EvidenceBatchExecution:
    return planner.execute_investigation_step(req, _planner_deps())


def get_active_evidence_batch(req: GetActiveEvidenceBatchRequest) -> ActiveEvidenceBatchContract:
    return planner.get_active_evidence_batch_contract(req)


def submit_evidence_step_artifacts(req: SubmitEvidenceArtifactsRequest) -> SubmittedEvidenceReconciliationResult:
    return planner.submit_evidence_step_artifacts(req)


def update_investigation_plan(req: UpdateInvestigationPlanRequest) -> InvestigationPlan:
    return planner.update_investigation_plan(req)


def _reporting_execution_context(
    req: InvestigationReportRequest | InvestigationReportingRequest,
    incident: BuildInvestigationPlanRequest,
) -> tuple[InvestigationPlan, InvestigationPlan, list[EvidenceBatchExecution], bool]:
    context = getattr(req, "execution_context", None)
    if context is None:
        plan = build_investigation_plan(incident)
        return plan, plan, [], True

    _validate_execution_context_matches_incident(context, incident)
    initial_plan = context.initial_plan or build_investigation_plan(incident)
    return (
        initial_plan,
        context.updated_plan,
        list(context.executions),
        context.allow_bounded_fallback_execution,
    )


def _runtime_context(
    *,
    initial_plan: InvestigationPlan,
    updated_plan: InvestigationPlan,
    executions: list[EvidenceBatchExecution],
    allow_bounded_fallback_execution: bool = False,
) -> ReportingExecutionContext:
    return ReportingExecutionContext(
        initial_plan=initial_plan,
        updated_plan=updated_plan,
        executions=executions,
        allow_bounded_fallback_execution=allow_bounded_fallback_execution,
    )


def _incident_fingerprint(incident: BuildInvestigationPlanRequest) -> str:
    encoded = json.dumps(incident.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _encode_handoff_token(
    *,
    incident: BuildInvestigationPlanRequest,
    context: ReportingExecutionContext,
) -> str:
    payload = {
        "version": _HANDOFF_TOKEN_VERSION,
        "incident_fingerprint": _incident_fingerprint(incident),
        "context": context.model_dump(mode="json"),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(raw)).decode("ascii")


def _decode_handoff_token(
    *,
    incident: BuildInvestigationPlanRequest,
    token: str,
) -> ReportingExecutionContext:
    try:
        decoded = zlib.decompress(base64.urlsafe_b64decode(token.encode("ascii")))
        payload = json.loads(decoded.decode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive decode guard
        raise ValueError("invalid handoff_token") from exc
    if payload.get("version") != _HANDOFF_TOKEN_VERSION:
        raise ValueError("unsupported handoff_token version")
    if payload.get("incident_fingerprint") != _incident_fingerprint(incident):
        raise ValueError("handoff_token does not match the supplied incident")
    context_payload = payload.get("context")
    if not isinstance(context_payload, dict):
        raise ValueError("handoff_token is missing runtime context")
    return ReportingExecutionContext.model_validate(context_payload)


def _handoff_runtime_context(req: HandoffActiveEvidenceBatchRequest) -> ReportingExecutionContext:
    if req.handoff_token and req.execution_context is not None:
        raise ValueError("provide either handoff_token or execution_context, not both")
    if req.handoff_token:
        return _decode_handoff_token(incident=req.incident, token=req.handoff_token).model_copy(
            update={"allow_bounded_fallback_execution": False}
        )
    if req.execution_context is not None:
        _validate_execution_context_matches_incident(req.execution_context, req.incident)
        return req.execution_context.model_copy(update={"allow_bounded_fallback_execution": False})
    plan = build_investigation_plan(req.incident)
    return _runtime_context(initial_plan=plan, updated_plan=plan, executions=[], allow_bounded_fallback_execution=False)


def _validate_execution_context_matches_incident(
    context: ReportingExecutionContext,
    incident: BuildInvestigationPlanRequest,
) -> None:
    baseline_plan = context.initial_plan or context.updated_plan
    if not _incident_matches_plan_identity(incident, baseline_plan):
        raise ValueError("execution_context does not match the supplied incident")


def _incident_matches_plan_identity(
    incident: BuildInvestigationPlanRequest,
    plan: InvestigationPlan,
) -> bool:
    target = plan.target
    if incident.target:
        if target is None:
            return False
        if incident.target not in {target.requested_target, target.target}:
            return False
    if incident.namespace is not None and (target is None or target.namespace != incident.namespace):
        return False
    if incident.cluster is not None and (target is None or target.cluster != incident.cluster):
        return False
    if incident.service_name is not None and (target is None or target.service_name != incident.service_name):
        return False
    if incident.node_name is not None and (target is None or target.node_name != incident.node_name):
        return False
    if incident.alertname and plan.mode != "alert_rca":
        return False
    return True


def _active_batch_contract_or_none(
    *,
    plan: InvestigationPlan,
    incident: BuildInvestigationPlanRequest,
    batch_id: str | None = None,
) -> ActiveEvidenceBatchContract | None:
    if plan.active_batch_id is None:
        return None
    return planner.get_active_evidence_batch_contract(
        GetActiveEvidenceBatchRequest(
            plan=plan,
            incident=incident,
            batch_id=batch_id or plan.active_batch_id,
        )
    )


def _required_external_step_ids(batch: ActiveEvidenceBatchContract | None) -> list[str]:
    if batch is None:
        return []
    return [step.step_id for step in batch.steps if step.execution_mode == "external_preferred"]


def _handoff_guidance(
    batch: ActiveEvidenceBatchContract | None,
) -> tuple[str, str, list[str]]:
    required_external_step_ids = _required_external_step_ids(batch)
    if batch is None:
        return "complete", "render_report", []
    if required_external_step_ids:
        return "awaiting_external_submission", "submit_external_steps", required_external_step_ids
    return "ready_for_next_handoff", "call_handoff_again", []


def build_investigation_state(req: InvestigationReportingRequest) -> InvestigationState:
    incident = _report_request_to_plan_request(req)
    initial_plan, updated_plan, executions, allow_bounded_fallback_execution = _reporting_execution_context(req, incident)
    if updated_plan.mode == "factual_analysis":
        raise ValueError("state-backed RCA analysis is not supported for factual_analysis plans")
    if updated_plan.target is None:
        raise ValueError("investigation plan did not produce a primary target")
    state = build_investigation_state_artifact(
        incident=incident,
        initial_plan=initial_plan,
        updated_plan=updated_plan,
        executions=executions,
    )
    if state.primary_evidence is not None or not allow_bounded_fallback_execution:
        return state

    fallback_execution = execute_investigation_step(
        ExecuteInvestigationStepRequest(
            plan=updated_plan,
            incident=incident,
        )
    )
    fallback_updated_plan = update_investigation_plan(
        UpdateInvestigationPlanRequest(plan=updated_plan, execution=fallback_execution)
    )
    return build_investigation_state_artifact(
        incident=incident,
        initial_plan=initial_plan,
        updated_plan=fallback_updated_plan,
        executions=[*executions, fallback_execution],
    )


def advance_investigation_runtime(req: AdvanceInvestigationRuntimeRequest) -> AdvanceInvestigationRuntimeResponse:
    initial_plan, updated_plan, executions, _allow_bounded_fallback_execution = _reporting_execution_context(
        InvestigationReportingRequest(
            **req.incident.model_dump(mode="python"),
            execution_context=req.execution_context,
        ),
        req.incident,
    )
    result = planner.advance_active_evidence_batch(
        plan=updated_plan,
        incident=req.incident,
        submitted_steps=req.submitted_steps,
        batch_id=req.batch_id,
        deps=_planner_deps(),
    )
    next_active_batch = _active_batch_contract_or_none(
        plan=result.updated_plan,
        incident=req.incident,
        batch_id=result.updated_plan.active_batch_id,
    )
    return AdvanceInvestigationRuntimeResponse(
        execution_context=_runtime_context(
            initial_plan=initial_plan,
            updated_plan=result.updated_plan,
            executions=[*executions, result.execution],
        ),
        next_active_batch=next_active_batch,
    )


def handoff_active_evidence_batch(req: HandoffActiveEvidenceBatchRequest) -> HandoffActiveEvidenceBatchResponse:
    seeded_context = _handoff_runtime_context(req)
    initial_plan = seeded_context.initial_plan or build_investigation_plan(req.incident)
    updated_plan = seeded_context.updated_plan
    executions = list(seeded_context.executions)
    active_batch = _active_batch_contract_or_none(
        plan=updated_plan,
        incident=req.incident,
        batch_id=req.batch_id,
    )
    if active_batch is None:
        handoff_status, next_action, required_external_step_ids = _handoff_guidance(active_batch)
        return HandoffActiveEvidenceBatchResponse(
            execution_context=seeded_context,
            handoff_token=_encode_handoff_token(incident=req.incident, context=seeded_context),
            active_batch=None,
            execution=None,
            handoff_status=handoff_status,
            next_action=next_action,
            required_external_step_ids=required_external_step_ids,
        )

    requires_external_submission = any(step.execution_mode == "external_preferred" for step in active_batch.steps)
    if requires_external_submission and not req.submitted_steps:
        handoff_status, next_action, required_external_step_ids = _handoff_guidance(active_batch)
        return HandoffActiveEvidenceBatchResponse(
            execution_context=seeded_context,
            handoff_token=_encode_handoff_token(incident=req.incident, context=seeded_context),
            active_batch=active_batch,
            execution=None,
            handoff_status=handoff_status,
            next_action=next_action,
            required_external_step_ids=required_external_step_ids,
        )

    result = planner.advance_active_evidence_batch(
        plan=updated_plan,
        incident=req.incident,
        submitted_steps=req.submitted_steps,
        batch_id=req.batch_id,
        deps=_planner_deps(),
    )
    next_active_batch = _active_batch_contract_or_none(
        plan=result.updated_plan,
        incident=req.incident,
        batch_id=result.updated_plan.active_batch_id,
    )
    handoff_status, next_action, required_external_step_ids = _handoff_guidance(next_active_batch)
    returned_context = _runtime_context(
        initial_plan=initial_plan,
        updated_plan=result.updated_plan,
        executions=[*executions, result.execution],
    )
    return HandoffActiveEvidenceBatchResponse(
        execution_context=returned_context,
        handoff_token=_encode_handoff_token(incident=req.incident, context=returned_context),
        active_batch=next_active_batch,
        execution=result.execution,
        handoff_status=handoff_status,
        next_action=next_action,
        required_external_step_ids=required_external_step_ids,
    )


def rank_hypotheses_from_state(state: InvestigationState) -> InvestigationAnalysis:
    if state.target is None or state.primary_evidence is None:
        raise ValueError("investigation state does not have enough evidence to rank hypotheses")

    analysis = build_investigation_analysis(state.primary_evidence, state.target)
    extra_limitations: list[str] = []
    for artifact in state.artifacts:
        if artifact.evidence_bundle is None or artifact.evidence_bundle == state.primary_evidence:
            continue
        extra_limitations.extend(artifact.limitations)

    suggested_follow_ups = list(analysis.suggested_follow_ups)
    if state.change_candidates is not None and state.change_candidates.changes:
        suggested_follow_ups.append("Validate recent changes against the leading hypothesis before taking write actions.")

    return analysis.model_copy(
        update={
            "limitations": _dedupe_preserving_order([*analysis.limitations, *extra_limitations]),
            "suggested_follow_ups": _dedupe_preserving_order(suggested_follow_ups),
        }
    )


def _apply_guidelines(
    analysis: InvestigationAnalysis,
    *,
    target: InvestigationTarget,
    alertname: str | None,
) -> tuple[str, list[str], list[ResolvedGuideline], list[str]]:
    rules, load_limitations = load_guideline_rules()
    context = guideline_context_from_analysis(analysis, target, alertname=alertname)
    resolved = resolve_guidelines_for_context(rules, context)
    if not resolved:
        return analysis.recommended_next_step, list(analysis.suggested_follow_ups), [], load_limitations

    recommended_next_step = next(
        (item.text for item in resolved if item.category == "next_step"),
        analysis.recommended_next_step,
    )
    suggested_follow_ups = list(analysis.suggested_follow_ups)
    for item in resolved:
        if item.category == "next_step" and item.text == recommended_next_step:
            continue
        suggested_follow_ups.append(item.text)

    return recommended_next_step, _dedupe_preserving_order(suggested_follow_ups), resolved, load_limitations


def _render_investigation_report_from_analysis(
    analysis: InvestigationAnalysis,
    *,
    normalization_notes: list[str],
    supplemental_evidence: list[str],
    related_data: list[CorrelatedChange],
    related_data_note: str | None,
    limitations: list[str],
    recommended_next_step: str,
    suggested_follow_ups: list[str],
    guidelines: list[ResolvedGuideline],
    tool_path_trace=None,
) -> InvestigationReport:
    lead = primary_hypothesis(analysis)
    effective_confidence = adjusted_confidence_from_hypotheses(analysis)
    return InvestigationReport(
        cluster=analysis.cluster,
        scope=analysis.scope,
        target=analysis.target,
        diagnosis=lead.diagnosis,
        likely_cause=lead.likely_cause,
        confidence=effective_confidence,
        evidence=_dedupe_preserving_order([*supplemental_evidence, *rendered_evidence_from_hypothesis(lead)]),
        evidence_items=lead.evidence_items,
        related_data=related_data,
        related_data_note=related_data_note,
        limitations=sorted(set(limitations)),
        recommended_next_step=recommended_next_step,
        suggested_follow_ups=_dedupe_preserving_order(suggested_follow_ups),
        guidelines=guidelines,
        normalization_notes=normalization_notes,
        tool_path_trace=tool_path_trace,
    )


def render_investigation_report_from_state(
    state: InvestigationState,
    *,
    include_related_data: bool = True,
    correlation_window_minutes: int = 60,
    correlation_limit: int = 10,
    anchor_timestamp: str | None = None,
    alertname: str | None = None,
) -> InvestigationReport:
    if state.target is None:
        raise ValueError("investigation state does not have a resolved target")

    analysis = rank_hypotheses_from_state(state)
    related_data: list[CorrelatedChange] = []
    related_data_note: str | None = None
    limitations = list(analysis.limitations)
    limitations.extend(ambiguity_limitations_from_hypotheses(analysis))
    recommended_next_step, suggested_follow_ups, guidelines, guideline_limitations = _apply_guidelines(
        analysis,
        target=state.target,
        alertname=alertname,
    )
    suggested_follow_ups.extend(follow_ups_from_hypotheses(analysis))
    limitations.extend(guideline_limitations)

    if include_related_data:
        correlated = state.change_candidates
        if correlated is None:
            correlated = collect_correlated_changes_for_target(
                state.target,
                lookback_minutes=correlation_window_minutes,
                anchor_timestamp=anchor_timestamp,
                limit=correlation_limit,
            )
        related_data, related_data_note = _filter_related_data_from_evidence(
            primary_hypothesis(analysis).evidence_items,
            correlated.changes,
        )
        correlation_limitations = list(correlated.limitations)
        if not related_data and related_data_note:
            correlation_limitations = [
                item for item in correlation_limitations if not _is_empty_correlation_limitation(item)
            ]
        limitations.extend(correlation_limitations)
        if related_data:
            suggested_follow_ups.append("Inspect the related changes timeline before taking write actions.")

    return _render_investigation_report_from_analysis(
        analysis,
        normalization_notes=list(state.target.normalization_notes),
        supplemental_evidence=_supplemental_evidence_from_state(state),
        related_data=related_data,
        related_data_note=related_data_note,
        limitations=limitations,
        recommended_next_step=recommended_next_step,
        suggested_follow_ups=suggested_follow_ups,
        guidelines=guidelines,
        tool_path_trace=state.tool_path_trace,
    )


def normalize_incident_input(req: InvestigationReportRequest) -> InvestigationTarget:
    normalized = planner.normalized_request(req, _planner_deps())
    return planner.investigation_target_from_normalized(
        normalized,
        requested_target=req.target or normalized.target,
    )


def resolve_primary_target(req: InvestigationReportRequest) -> InvestigationTarget:
    return planner.resolve_primary_target(req, _planner_deps())


def rank_hypotheses(req: InvestigationReportingRequest) -> InvestigationAnalysis:
    return rank_hypotheses_from_state(build_investigation_state(req))


def render_investigation_report(req: InvestigationReportingRequest) -> InvestigationReport:
    return render_investigation_report_from_state(
        build_investigation_state(req),
        include_related_data=req.include_related_data,
        correlation_window_minutes=req.correlation_window_minutes,
        correlation_limit=req.correlation_limit,
        anchor_timestamp=req.anchor_timestamp,
        alertname=req.alertname,
    )
