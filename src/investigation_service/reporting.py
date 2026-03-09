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
    AlertInvestigationReportRequest,
    BuildInvestigationPlanRequest,
    BuildRootCauseReportRequest,
    CorrelatedChange,
    EvidenceBatchExecution,
    ExecuteInvestigationStepRequest,
    InvestigationAnalysis,
    InvestigationPlan,
    InvestigationReport,
    InvestigationReportRequest,
    InvestigationState,
    InvestigationTarget,
    ResolvedGuideline,
    RootCauseReport,
    UpdateInvestigationPlanRequest,
)
from .planner import PlannerDeps
from .routing import canonical_target, scope_from_target
from .state import build_investigation_state as build_investigation_state_artifact
from .tools import (
    collect_alert_context,
    collect_node_context,
    collect_service_context,
    collect_workload_context,
    evidence_bundle_from_context,
    find_unhealthy_pod,
    normalize_alert_input,
)
from . import planner

_EMPTY_CORRELATION_LIMITATION = "no correlated changes found in the requested time window"
_EMPTY_RELATED_DATA_NOTE = "no meaningful correlated changes found in the requested time window"


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
        collect_node_context=collect_node_context,
        collect_service_context=collect_service_context,
        collect_workload_context=collect_workload_context,
        collect_alert_evidence=lambda req: evidence_bundle_from_context(collect_alert_context(req)),
        collect_node_evidence=lambda req: evidence_bundle_from_context(collect_node_context(req)),
        collect_service_evidence=lambda req: evidence_bundle_from_context(collect_service_context(req)),
        collect_workload_evidence=lambda req: evidence_bundle_from_context(collect_workload_context(req)),
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


def _filter_related_data(report: RootCauseReport, changes: list[CorrelatedChange]) -> tuple[list[CorrelatedChange], str | None]:
    return _filter_related_data_from_evidence(report.evidence_items, changes)


def _report_request_to_plan_request(req: InvestigationReportRequest) -> BuildInvestigationPlanRequest:
    return BuildInvestigationPlanRequest(
        cluster=req.cluster,
        namespace=req.namespace,
        target=req.target,
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


def update_investigation_plan(req: UpdateInvestigationPlanRequest) -> InvestigationPlan:
    return planner.update_investigation_plan(req)


def build_investigation_state(req: InvestigationReportRequest) -> InvestigationState:
    incident = _report_request_to_plan_request(req)
    plan = build_investigation_plan(incident)
    if plan.mode == "factual_analysis":
        raise ValueError("state-backed RCA analysis is not supported for factual_analysis plans")
    if plan.target is None:
        raise ValueError("investigation plan did not produce a primary target")
    execution = execute_investigation_step(
        ExecuteInvestigationStepRequest(
            plan=plan,
            incident=incident,
        )
    )
    updated_plan = update_investigation_plan(UpdateInvestigationPlanRequest(plan=plan, execution=execution))
    return build_investigation_state_artifact(
        incident=incident,
        initial_plan=plan,
        updated_plan=updated_plan,
        executions=[execution],
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
    )


def build_root_cause_report(req: BuildRootCauseReportRequest) -> RootCauseReport:
    report = render_investigation_report(
        InvestigationReportRequest(
            cluster=req.cluster,
            namespace=req.namespace,
            target=req.target,
            profile=req.profile,
            service_name=req.service_name,
            lookback_minutes=req.lookback_minutes,
            include_related_data=False,
        )
    )
    return RootCauseReport(
        cluster=report.cluster,
        scope=report.scope,
        target=report.target,
        diagnosis=report.diagnosis,
        likely_cause=report.likely_cause,
        confidence=report.confidence,
        evidence=report.evidence,
        evidence_items=report.evidence_items,
        limitations=report.limitations,
        recommended_next_step=report.recommended_next_step,
        suggested_follow_ups=report.suggested_follow_ups,
    )


def normalize_incident_input(req: InvestigationReportRequest) -> InvestigationTarget:
    normalized = planner.normalized_request(req, _planner_deps())
    return planner.investigation_target_from_normalized(
        normalized,
        requested_target=req.target or normalized.target,
    )


def resolve_primary_target(req: InvestigationReportRequest) -> InvestigationTarget:
    return planner.resolve_primary_target(req, _planner_deps())


def rank_hypotheses(req: InvestigationReportRequest) -> InvestigationAnalysis:
    return rank_hypotheses_from_state(build_investigation_state(req))


def render_investigation_report(req: InvestigationReportRequest) -> InvestigationReport:
    return render_investigation_report_from_state(
        build_investigation_state(req),
        include_related_data=req.include_related_data,
        correlation_window_minutes=req.correlation_window_minutes,
        correlation_limit=req.correlation_limit,
        anchor_timestamp=req.anchor_timestamp,
        alertname=req.alertname,
    )


def build_alert_investigation_report(req: AlertInvestigationReportRequest) -> InvestigationReport:
    return render_investigation_report(
        InvestigationReportRequest(
            cluster=req.cluster,
            namespace=req.namespace,
            target=req.target,
            profile=req.profile,
            service_name=req.service_name,
            lookback_minutes=req.lookback_minutes,
            include_related_data=req.include_related_data,
            correlation_window_minutes=req.correlation_window_minutes,
            correlation_limit=req.correlation_limit,
            anchor_timestamp=req.anchor_timestamp,
            alertname=req.alertname,
            labels=req.labels,
            annotations=req.annotations,
            node_name=req.node_name,
        )
    )


def build_investigation_report(req: InvestigationReportRequest) -> InvestigationReport:
    return render_investigation_report(req)
