from .analysis import (
    build_investigation_analysis,
    build_primary_evidence_from_bundle,
    primary_hypothesis,
    rendered_evidence_from_hypothesis,
)
from .models import (
    CollectContextRequest,
    CollectedContextResponse,
    EvidenceBundle,
    InvestigationAnalysis,
    InvestigationTarget,
    NormalizedInvestigationRequest,
    RootCauseReport,
)
from .routing import scope_from_target


def _context_from_bundle(bundle: EvidenceBundle) -> CollectedContextResponse:
    return CollectedContextResponse(
        cluster=bundle.cluster,
        target=bundle.target,
        object_state=bundle.object_state,
        events=bundle.events,
        log_excerpt=bundle.log_excerpt,
        metrics=bundle.metrics,
        findings=bundle.findings,
        limitations=bundle.limitations,
        enrichment_hints=bundle.enrichment_hints,
    )


def _target_from_request(request: NormalizedInvestigationRequest | CollectContextRequest) -> InvestigationTarget:
    if isinstance(request, NormalizedInvestigationRequest):
        return InvestigationTarget(
            source=request.source,
            scope=request.scope,
            cluster=request.cluster,
            namespace=request.namespace,
            requested_target=request.target,
            target=request.target,
            node_name=request.node_name,
            service_name=request.service_name,
            profile=request.profile,
            lookback_minutes=request.lookback_minutes,
            normalization_notes=list(request.normalization_notes),
        )

    scope = scope_from_target(request.target, request.profile)
    node_name = request.target.split("/", 1)[1] if scope == "node" and "/" in request.target else None
    service_name = request.service_name or (request.target.split("/", 1)[1] if scope == "service" and "/" in request.target else None)
    return InvestigationTarget(
        source="manual",
        scope=scope,
        cluster=request.cluster,
        namespace=request.namespace,
        requested_target=request.target,
        target=request.target,
        node_name=node_name,
        service_name=service_name,
        profile=request.profile,
        lookback_minutes=request.lookback_minutes,
        normalization_notes=[],
    )


def build_primary_evidence(context: CollectedContextResponse, scope: str) -> list:
    bundle = EvidenceBundle(
        cluster=context.cluster,
        target=context.target,
        object_state=context.object_state,
        events=context.events,
        log_excerpt=context.log_excerpt,
        metrics=context.metrics,
        findings=context.findings,
        limitations=context.limitations,
        enrichment_hints=context.enrichment_hints,
    )
    return build_primary_evidence_from_bundle(bundle, scope)


def render_root_cause_from_analysis(analysis: InvestigationAnalysis) -> RootCauseReport:
    lead = primary_hypothesis(analysis)
    return RootCauseReport(
        cluster=analysis.cluster,
        scope=analysis.scope,
        target=analysis.target,
        diagnosis=lead.diagnosis,
        likely_cause=lead.likely_cause,
        confidence=lead.confidence,
        evidence=rendered_evidence_from_hypothesis(lead),
        evidence_items=lead.evidence_items,
        limitations=analysis.limitations,
        recommended_next_step=analysis.recommended_next_step,
        suggested_follow_ups=analysis.suggested_follow_ups,
    )


def synthesize_root_cause(bundle: EvidenceBundle, target: InvestigationTarget) -> RootCauseReport:
    analysis = build_investigation_analysis(bundle, target)
    return render_root_cause_from_analysis(analysis)


def build_root_cause_report(
    context: CollectedContextResponse, request: NormalizedInvestigationRequest | CollectContextRequest
) -> RootCauseReport:
    bundle = EvidenceBundle(
        cluster=context.cluster,
        target=context.target,
        object_state=context.object_state,
        events=context.events,
        log_excerpt=context.log_excerpt,
        metrics=context.metrics,
        findings=context.findings,
        limitations=context.limitations,
        enrichment_hints=context.enrichment_hints,
    )
    target = _target_from_request(request)
    return synthesize_root_cause(bundle, target)
