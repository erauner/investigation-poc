from .correlation import collect_correlated_changes
from .guidelines import load_guideline_rules, resolve_guidelines
from .models import (
    BuildRootCauseReportRequest,
    CollectAlertContextRequest,
    CollectContextRequest,
    CollectCorrelatedChangesRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    CorrelatedChange,
    FindUnhealthyPodRequest,
    InvestigationReport,
    InvestigationReportRequest,
    NormalizedInvestigationRequest,
    ResolvedGuideline,
    RootCauseReport,
)
from .routing import canonical_target, scope_from_target
from .synthesis import build_root_cause_report as build_root_cause_report_impl
from .tools import (
    collect_node_context,
    collect_service_context,
    collect_workload_context,
    find_unhealthy_pod,
    normalize_alert_input,
)

_VAGUE_WORKLOAD_TARGETS = {
    "pod",
    "pods",
    "workload",
    "workloads",
    "unhealthy",
    "unhealthy-pod",
    "unhealthy-workload",
}

_EMPTY_CORRELATION_LIMITATION = "no correlated changes found in the requested time window"


def _is_empty_correlation_limitation(value: str) -> bool:
    normalized = value.strip().lower()
    return "correlated changes" in normalized and "requested time window" in normalized


def _normalized_request(req: InvestigationReportRequest) -> NormalizedInvestigationRequest:
    if req.alertname:
        return normalize_alert_input(
            CollectAlertContextRequest(
                alertname=req.alertname,
                labels=req.labels,
                annotations=req.annotations,
                namespace=req.namespace,
                node_name=req.node_name,
                target=req.target,
                profile=req.profile,
                service_name=req.service_name,
                lookback_minutes=req.lookback_minutes,
            )
        )

    if not req.target:
        raise ValueError("target is required when alertname is not supplied")

    target = canonical_target(req.target, req.profile, req.service_name)
    scope = scope_from_target(target, req.profile)
    profile = req.profile
    notes = ["target normalized from manual request"]
    if scope == "service" and profile == "workload":
        profile = "service"
        notes.append("profile promoted to service based on target")

    return NormalizedInvestigationRequest(
        source="manual",
        scope=scope,
        namespace=req.namespace,
        target=target,
        node_name=target.split("/", 1)[1] if scope == "node" and "/" in target else None,
        service_name=(req.service_name or target.split("/", 1)[1]) if scope == "service" and "/" in target else None,
        profile=profile,
        lookback_minutes=req.lookback_minutes,
        normalization_notes=notes,
    )


def _resolve_vague_workload_target(normalized: NormalizedInvestigationRequest) -> NormalizedInvestigationRequest:
    if normalized.scope != "workload":
        return normalized

    lowered = normalized.target.strip().lower()
    if lowered not in _VAGUE_WORKLOAD_TARGETS:
        return normalized
    if not normalized.namespace:
        raise ValueError("namespace is required when resolving a vague workload target")

    unhealthy = find_unhealthy_pod(FindUnhealthyPodRequest(namespace=normalized.namespace))
    candidate = unhealthy.candidate
    if candidate is None:
        raise ValueError("no unhealthy pod found in namespace")

    notes = list(normalized.normalization_notes)
    notes.append(f"resolved vague workload target to {candidate.target}")
    return normalized.model_copy(update={"target": candidate.target, "normalization_notes": notes})


def _collect_context_for_normalized_request(normalized: NormalizedInvestigationRequest):
    if normalized.scope == "node":
        return collect_node_context(
            CollectNodeContextRequest(
                node_name=normalized.node_name or normalized.target.split("/", 1)[1],
                lookback_minutes=normalized.lookback_minutes,
            )
        )
    if normalized.scope == "service":
        if not normalized.namespace:
            raise ValueError("namespace is required for service investigations")
        service_name = normalized.service_name or normalized.target.split("/", 1)[1]
        return collect_service_context(
            CollectServiceContextRequest(
                namespace=normalized.namespace,
                service_name=service_name,
                target=normalized.target,
                lookback_minutes=normalized.lookback_minutes,
            )
        )
    return collect_workload_context(
        CollectContextRequest(
            namespace=normalized.namespace,
            target=normalized.target,
            profile=normalized.profile,
            service_name=normalized.service_name,
            lookback_minutes=normalized.lookback_minutes,
        )
    )


def _filter_related_data(report: RootCauseReport, changes: list[CorrelatedChange]) -> tuple[list[CorrelatedChange], str | None]:
    primary_fingerprints = {item.fingerprint for item in report.evidence_items}
    filtered = [change for change in changes if change.fingerprint not in primary_fingerprints]
    omitted = len(changes) - len(filtered)
    if filtered:
        if omitted:
            return filtered, f"{omitted} correlated change omitted because it duplicated primary evidence"
        return filtered, None
    if changes:
        return [], "all correlated changes duplicated primary evidence"
    return [], "no meaningful correlated changes found in the requested time window"


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


def _apply_guidelines(
    root_cause: RootCauseReport,
    *,
    alertname: str | None,
    namespace: str | None,
    service_name: str | None,
) -> tuple[str, list[str], list[ResolvedGuideline], list[str]]:
    base_report = _base_investigation_report(root_cause)
    rules, load_limitations = load_guideline_rules()
    resolved = resolve_guidelines(
        rules,
        base_report,
        alertname=alertname,
        namespace=namespace,
        service_name=service_name,
    )
    if not resolved:
        return root_cause.recommended_next_step, list(root_cause.suggested_follow_ups), [], load_limitations

    recommended_next_step = next(
        (item.text for item in resolved if item.category == "next_step"),
        root_cause.recommended_next_step,
    )
    suggested_follow_ups = list(root_cause.suggested_follow_ups)
    for item in resolved:
        if item.category == "next_step" and item.text == recommended_next_step:
            continue
        suggested_follow_ups.append(item.text)

    return recommended_next_step, _dedupe_preserving_order(suggested_follow_ups), resolved, load_limitations


def build_root_cause_report(req: BuildRootCauseReportRequest) -> RootCauseReport:
    report = build_investigation_report(
        InvestigationReportRequest(
            namespace=req.namespace,
            target=req.target,
            profile=req.profile,
            service_name=req.service_name,
            lookback_minutes=req.lookback_minutes,
            include_related_data=False,
        )
    )
    return RootCauseReport(
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


def build_investigation_report(req: InvestigationReportRequest) -> InvestigationReport:
    normalized = _resolve_vague_workload_target(_normalized_request(req))
    context = _collect_context_for_normalized_request(normalized)
    root_cause = build_root_cause_report_impl(context, normalized)

    related_data: list[CorrelatedChange] = []
    related_data_note: str | None = None
    limitations = list(root_cause.limitations)
    recommended_next_step, suggested_follow_ups, guidelines, guideline_limitations = _apply_guidelines(
        root_cause,
        alertname=req.alertname,
        namespace=normalized.namespace,
        service_name=normalized.service_name,
    )
    limitations.extend(guideline_limitations)

    if req.include_related_data:
        correlated = collect_correlated_changes(
            CollectCorrelatedChangesRequest(
                namespace=normalized.namespace,
                target=normalized.target,
                profile=normalized.profile,
                service_name=normalized.service_name,
                lookback_minutes=req.correlation_window_minutes,
                anchor_timestamp=req.anchor_timestamp,
                limit=req.correlation_limit,
            )
        )
        related_data, related_data_note = _filter_related_data(root_cause, correlated.changes)
        correlation_limitations = list(correlated.limitations)
        if not related_data and related_data_note:
            correlation_limitations = [
                item for item in correlation_limitations if not _is_empty_correlation_limitation(item)
            ]
        limitations.extend(correlation_limitations)
        if related_data:
            suggested_follow_ups.append("Inspect the related changes timeline before taking write actions.")

    return InvestigationReport(
        scope=root_cause.scope,
        target=root_cause.target,
        diagnosis=root_cause.diagnosis,
        likely_cause=root_cause.likely_cause,
        confidence=root_cause.confidence,
        evidence=root_cause.evidence,
        evidence_items=root_cause.evidence_items,
        related_data=related_data,
        related_data_note=related_data_note,
        limitations=sorted(set(limitations)),
        recommended_next_step=recommended_next_step,
        suggested_follow_ups=_dedupe_preserving_order(suggested_follow_ups),
        guidelines=guidelines,
        normalization_notes=normalized.normalization_notes,
    )
