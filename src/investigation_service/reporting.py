from dataclasses import dataclass

from .correlation import collect_correlated_changes, collect_correlated_changes_for_target
from .event_fingerprints import canonicalize_event_fingerprint
from .guidelines import (
    guideline_context_from_analysis,
    load_guideline_rules,
    resolve_guidelines,
    resolve_guidelines_for_context,
)
from .analysis import (
    adjusted_confidence_from_hypotheses,
    ambiguity_limitations_from_hypotheses,
    build_investigation_analysis,
    follow_ups_from_hypotheses,
    primary_hypothesis,
    rendered_evidence_from_hypothesis,
)
from .models import (
    AlertInvestigationReportRequest,
    BuildInvestigationPlanRequest,
    BuildRootCauseReportRequest,
    CollectCorrelatedChangesRequest,
    CorrelatedChange,
    EvidenceBundle,
    Hypothesis,
    InvestigationAnalysis,
    InvestigationPlan,
    InvestigationTarget,
    InvestigationReport,
    InvestigationReportRequest,
    NormalizedInvestigationRequest,
    ResolvedGuideline,
    RootCauseReport,
    TargetRef,
)
from .cluster_registry import resolve_cluster
from .k8s_adapter import get_backend_cr, get_cluster_cr, get_frontend_cr
from .planner import PlannerDeps
from . import planner
from .routing import canonical_target, scope_from_target
from .synthesis import build_root_cause_report as build_root_cause_report_impl
from .synthesis import synthesize_root_cause as synthesize_root_cause_impl
from .tools import (
    collect_node_context,
    collect_service_context,
    collect_workload_context,
    evidence_bundle_from_context,
    find_unhealthy_pod,
    normalize_alert_input,
)

_EMPTY_CORRELATION_LIMITATION = "no correlated changes found in the requested time window"
_EMPTY_RELATED_DATA_NOTE = "no meaningful correlated changes found in the requested time window"
_LEGACY_BUILD_ROOT_CAUSE_IMPL = build_root_cause_report_impl


@dataclass(frozen=True)
class CollectedInvestigationState:
    plan: InvestigationPlan
    target: InvestigationTarget
    normalized: NormalizedInvestigationRequest
    context: object
    evidence: object


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
    )


def _collect_context_for_normalized_request(normalized: NormalizedInvestigationRequest):
    return planner.collect_context_for_normalized_request(normalized, _planner_deps())


def _align_normalized_request_with_context(
    normalized: NormalizedInvestigationRequest, context
) -> NormalizedInvestigationRequest:
    return planner.align_normalized_request_with_context(normalized, context)


def _filter_related_data(report: RootCauseReport, changes: list[CorrelatedChange]) -> tuple[list[CorrelatedChange], str | None]:
    return _filter_related_data_from_evidence(report.evidence_items, changes)


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


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _base_investigation_report(root_cause: RootCauseReport) -> InvestigationReport:
    return InvestigationReport(
        cluster=root_cause.cluster,
        scope=root_cause.scope,
        target=root_cause.target,
        diagnosis=root_cause.diagnosis,
        likely_cause=root_cause.likely_cause,
        confidence=root_cause.confidence,
        evidence=root_cause.evidence,
        evidence_items=root_cause.evidence_items,
        related_data=[],
        related_data_note=None,
        limitations=root_cause.limitations,
        recommended_next_step=root_cause.recommended_next_step,
        suggested_follow_ups=root_cause.suggested_follow_ups,
        guidelines=[],
        normalization_notes=[],
    )


def _analysis_from_root_cause(root_cause: RootCauseReport) -> InvestigationAnalysis:
    return InvestigationAnalysis(
        cluster=root_cause.cluster,
        scope=root_cause.scope,
        target=root_cause.target,
        profile=root_cause.scope,
        hypotheses=[
            Hypothesis(
                key="legacy-root-cause",
                diagnosis=root_cause.diagnosis,
                likely_cause=root_cause.likely_cause,
                confidence=root_cause.confidence,
                score=0,
                supporting_findings=[],
                evidence_items=root_cause.evidence_items,
            )
        ],
        limitations=list(root_cause.limitations),
        recommended_next_step=root_cause.recommended_next_step,
        suggested_follow_ups=list(root_cause.suggested_follow_ups),
    )


def _base_investigation_report_from_analysis(analysis: InvestigationAnalysis) -> InvestigationReport:
    lead = primary_hypothesis(analysis)
    return InvestigationReport(
        cluster=analysis.cluster,
        scope=analysis.scope,
        target=analysis.target,
        diagnosis=lead.diagnosis,
        likely_cause=lead.likely_cause,
        confidence=lead.confidence,
        evidence=rendered_evidence_from_hypothesis(lead),
        evidence_items=lead.evidence_items,
        related_data=[],
        related_data_note=None,
        limitations=list(analysis.limitations),
        recommended_next_step=analysis.recommended_next_step,
        suggested_follow_ups=list(analysis.suggested_follow_ups),
        guidelines=[],
        normalization_notes=[],
    )


def _build_correlation_request(
    req: InvestigationReportRequest,
    target: InvestigationTarget,
) -> CollectCorrelatedChangesRequest:
    return CollectCorrelatedChangesRequest(
        cluster=target.cluster,
        namespace=target.namespace,
        target=target.target,
        profile=target.profile,
        service_name=target.service_name,
        lookback_minutes=req.correlation_window_minutes,
        anchor_timestamp=req.anchor_timestamp,
        limit=req.correlation_limit,
    )


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


def _collect_analysis_state(req: InvestigationReportRequest) -> CollectedInvestigationState:
    plan = build_investigation_plan(_report_request_to_plan_request(req))
    if plan.mode == "factual_analysis":
        raise ValueError("rendering an investigation report is not supported for factual_analysis plans")
    if plan.target is None:
        raise ValueError("investigation plan did not produce a primary target")

    normalized = planner.normalized_request_from_target(plan.target)
    context = _collect_context_for_normalized_request(normalized)
    normalized = _align_normalized_request_with_context(normalized, context)
    context_cluster = getattr(context, "cluster", None)
    if context_cluster and not any(note.startswith("cluster resolved") for note in normalized.normalization_notes):
        notes = list(normalized.normalization_notes)
        notes.append(f"cluster resolved from collected context: {context_cluster}")
        normalized = normalized.model_copy(update={"cluster": context_cluster, "normalization_notes": notes})
    target = planner.investigation_target_from_normalized(
        normalized,
        requested_target=plan.target.requested_target,
    )
    try:
        evidence = evidence_bundle_from_context(context)
    except AttributeError:
        evidence = EvidenceBundle(
            cluster=getattr(context, "cluster", normalized.cluster or "current-context"),
            target=getattr(context, "target", None)
            or TargetRef(
                namespace=normalized.namespace,
                kind="service" if normalized.scope == "service" else ("node" if normalized.scope == "node" else "pod"),
                name=(normalized.target.split("/", 1)[1] if "/" in normalized.target else normalized.target),
            ),
            object_state=getattr(context, "object_state", {}),
            events=getattr(context, "events", []),
            log_excerpt=getattr(context, "log_excerpt", ""),
            metrics=getattr(context, "metrics", {}),
            findings=getattr(context, "findings", []),
            limitations=getattr(context, "limitations", []),
            enrichment_hints=getattr(context, "enrichment_hints", []),
        )

    return CollectedInvestigationState(
        plan=plan,
        target=target,
        normalized=normalized,
        context=context,
        evidence=evidence,
    )


def _render_investigation_report_from_analysis(
    analysis: InvestigationAnalysis,
    *,
    normalization_notes: list[str],
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
        evidence=rendered_evidence_from_hypothesis(lead),
        evidence_items=lead.evidence_items,
        related_data=related_data,
        related_data_note=related_data_note,
        limitations=sorted(set(limitations)),
        recommended_next_step=recommended_next_step,
        suggested_follow_ups=_dedupe_preserving_order(suggested_follow_ups),
        guidelines=guidelines,
        normalization_notes=normalization_notes,
    )


def _synthesize_root_cause(state: CollectedInvestigationState) -> RootCauseReport:
    if build_root_cause_report_impl is not _LEGACY_BUILD_ROOT_CAUSE_IMPL:
        return build_root_cause_report_impl(state.context, state.normalized)
    return synthesize_root_cause_impl(state.evidence, state.target)


def _analyze_state(state: CollectedInvestigationState) -> InvestigationAnalysis:
    if build_root_cause_report_impl is not _LEGACY_BUILD_ROOT_CAUSE_IMPL:
        return _analysis_from_root_cause(_synthesize_root_cause(state))
    return build_investigation_analysis(state.evidence, state.target)


def _apply_guidelines(
    analysis: InvestigationAnalysis,
    *,
    target: InvestigationTarget,
    alertname: str | None,
) -> tuple[str, list[str], list[ResolvedGuideline], list[str]]:
    rules, load_limitations = load_guideline_rules()
    context = guideline_context_from_analysis(
        analysis,
        target,
        alertname=alertname,
    )
    resolved = resolve_guidelines_for_context(
        rules,
        context,
    )
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
    return _analyze_state(_collect_analysis_state(req))


def render_investigation_report(req: InvestigationReportRequest) -> InvestigationReport:
    state = _collect_analysis_state(req)
    analysis = _analyze_state(state)

    related_data: list[CorrelatedChange] = []
    related_data_note: str | None = None
    limitations = list(analysis.limitations)
    limitations.extend(ambiguity_limitations_from_hypotheses(analysis))
    recommended_next_step, suggested_follow_ups, guidelines, guideline_limitations = _apply_guidelines(
        analysis,
        target=state.target,
        alertname=req.alertname,
    )
    suggested_follow_ups.extend(follow_ups_from_hypotheses(analysis))
    limitations.extend(guideline_limitations)

    if req.include_related_data:
        if collect_correlated_changes.__module__ != "investigation_service.correlation":
            correlated = collect_correlated_changes(_build_correlation_request(req, state.target))
        else:
            correlated = collect_correlated_changes_for_target(
                state.target,
                lookback_minutes=req.correlation_window_minutes,
                anchor_timestamp=req.anchor_timestamp,
                limit=req.correlation_limit,
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
        normalization_notes=state.target.normalization_notes,
        related_data=related_data,
        related_data_note=related_data_note,
        limitations=limitations,
        recommended_next_step=recommended_next_step,
        suggested_follow_ups=suggested_follow_ups,
        guidelines=guidelines,
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
