from fastapi import FastAPI

from .models import (
    AlertInvestigationReportRequest,
    CollectAlertContextRequest,
    CollectCorrelatedChangesRequest,
    CollectContextRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    BuildRootCauseReportRequest,
    CollectedContextResponse,
    CorrelatedChangesResponse,
    EvidenceBundle,
    FindUnhealthyPodRequest,
    FindUnhealthyWorkloadsRequest,
    InvestigationAnalysis,
    InvestigationReport,
    InvestigationReportRequest,
    InvestigationTarget,
    InvestigateRequest,
    InvestigationResponse,
    RootCauseReport,
    UnhealthyPodResponse,
    UnhealthyWorkloadsResponse,
)
from .correlation import collect_change_candidates, collect_correlated_changes
from .reporting import (
    build_alert_investigation_report,
    build_investigation_report,
    build_root_cause_report as build_root_cause_report_from_request,
    normalize_incident_input as normalize_incident_input_from_request,
    rank_hypotheses as rank_hypotheses_from_request,
    render_investigation_report,
    resolve_primary_target as resolve_primary_target_from_request,
)
from .tools import (
    collect_alert_context,
    collect_alert_evidence,
    collect_node_context,
    collect_node_evidence,
    collect_service_context,
    collect_service_evidence,
    collect_workload_context,
    collect_workload_evidence,
    find_unhealthy_pod,
    find_unhealthy_workloads,
    normalize_alert_input,
)

app = FastAPI(title="Investigation Service", version="0.2.0")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/tools/collect_workload_context", response_model=CollectedContextResponse)
def collect_context(req: CollectContextRequest) -> CollectedContextResponse:
    return collect_workload_context(req)


@app.post("/tools/collect_alert_context", response_model=CollectedContextResponse)
def collect_alert(req: CollectAlertContextRequest) -> CollectedContextResponse:
    return collect_alert_context(req)


@app.post("/tools/normalize_alert_input")
def normalize_alert(req: CollectAlertContextRequest) -> dict:
    return normalize_alert_input(req).model_dump(mode="json")


@app.post("/tools/normalize_incident_input", response_model=InvestigationTarget)
def normalize_incident(req: InvestigationReportRequest) -> InvestigationTarget:
    return normalize_incident_input_from_request(req)


@app.post("/tools/resolve_primary_target", response_model=InvestigationTarget)
def resolve_target(req: InvestigationReportRequest) -> InvestigationTarget:
    return resolve_primary_target_from_request(req)


@app.post("/tools/collect_node_context", response_model=CollectedContextResponse)
def collect_node(req: CollectNodeContextRequest) -> CollectedContextResponse:
    return collect_node_context(req)


@app.post("/tools/collect_workload_evidence", response_model=EvidenceBundle)
def collect_workload_bundle(req: CollectContextRequest) -> EvidenceBundle:
    return collect_workload_evidence(req)


@app.post("/tools/collect_alert_evidence", response_model=EvidenceBundle)
def collect_alert_bundle(req: CollectAlertContextRequest) -> EvidenceBundle:
    return collect_alert_evidence(req)


@app.post("/tools/collect_node_evidence", response_model=EvidenceBundle)
def collect_node_bundle(req: CollectNodeContextRequest) -> EvidenceBundle:
    return collect_node_evidence(req)


@app.post("/tools/collect_service_context", response_model=CollectedContextResponse)
def collect_service(req: CollectServiceContextRequest) -> CollectedContextResponse:
    return collect_service_context(req)


@app.post("/tools/collect_service_evidence", response_model=EvidenceBundle)
def collect_service_bundle(req: CollectServiceContextRequest) -> EvidenceBundle:
    return collect_service_evidence(req)


@app.post("/tools/find_unhealthy_workloads", response_model=UnhealthyWorkloadsResponse)
def find_unhealthy(req: FindUnhealthyWorkloadsRequest) -> UnhealthyWorkloadsResponse:
    return find_unhealthy_workloads(req)


@app.post("/tools/find_unhealthy_pod", response_model=UnhealthyPodResponse)
def find_unhealthy_single(req: FindUnhealthyPodRequest) -> UnhealthyPodResponse:
    return find_unhealthy_pod(req)


@app.post("/tools/build_root_cause_report", response_model=RootCauseReport)
def build_report(req: BuildRootCauseReportRequest) -> RootCauseReport:
    return build_root_cause_report_from_request(req)


@app.post("/tools/rank_hypotheses", response_model=InvestigationAnalysis)
def rank_analysis(req: InvestigationReportRequest) -> InvestigationAnalysis:
    return rank_hypotheses_from_request(req)


@app.post("/tools/build_investigation_report", response_model=InvestigationReport)
def build_investigation(req: InvestigationReportRequest) -> InvestigationReport:
    return build_investigation_report(req)


@app.post("/tools/render_investigation_report", response_model=InvestigationReport)
def render_report(req: InvestigationReportRequest) -> InvestigationReport:
    return render_investigation_report(req)


@app.post("/tools/build_alert_investigation_report", response_model=InvestigationReport)
def build_alert_investigation(req: AlertInvestigationReportRequest) -> InvestigationReport:
    return build_alert_investigation_report(req)


@app.post("/tools/collect_correlated_changes", response_model=CorrelatedChangesResponse)
def collect_related(req: CollectCorrelatedChangesRequest) -> CorrelatedChangesResponse:
    return collect_correlated_changes(req)


@app.post("/tools/collect_change_candidates", response_model=CorrelatedChangesResponse)
def collect_change_candidates_route(req: CollectCorrelatedChangesRequest) -> CorrelatedChangesResponse:
    return collect_change_candidates(req)


@app.post("/investigate", response_model=InvestigationResponse)
def investigate(req: InvestigateRequest) -> InvestigationResponse:
    report = build_investigation_report(
        InvestigationReportRequest(
            cluster=req.cluster,
            namespace=req.namespace,
            target=req.target,
            profile=req.profile,
            service_name=req.service_name,
            lookback_minutes=req.lookback_minutes,
        )
    )
    evidence = [
        f"Cluster: {report.cluster}",
        f"Target: {report.target}",
        f"Profile: {req.profile}",
        *report.evidence,
        f"Limitations: {report.limitations}",
    ]
    return InvestigationResponse(
        diagnosis=report.diagnosis,
        evidence=evidence,
        recommendation=report.recommended_next_step,
    )
