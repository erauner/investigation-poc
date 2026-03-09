from fastapi import FastAPI

from .models import (
    BuildInvestigationPlanRequest,
    CollectAlertContextRequest,
    CollectCorrelatedChangesRequest,
    CorrelatedChangesResponse,
    EvidenceBatchExecution,
    ExecuteInvestigationStepRequest,
    FindUnhealthyPodRequest,
    FindUnhealthyWorkloadsRequest,
    InvestigationAnalysis,
    InvestigationPlan,
    InvestigationReport,
    InvestigationReportRequest,
    InvestigationTarget,
    UpdateInvestigationPlanRequest,
    UnhealthyPodResponse,
    UnhealthyWorkloadsResponse,
)
from .correlation import collect_change_candidates
from .reporting import (
    build_investigation_plan as build_investigation_plan_from_request,
    execute_investigation_step as execute_investigation_step_from_request,
    rank_hypotheses as rank_hypotheses_from_request,
    render_investigation_report,
    resolve_primary_target as resolve_primary_target_from_request,
    update_investigation_plan as update_investigation_plan_from_request,
)
from .tools import (
    find_unhealthy_pod,
    find_unhealthy_workloads,
    normalize_alert_input,
)

app = FastAPI(title="Investigation Service", version="0.2.0")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/tools/normalize_alert_input")
def normalize_alert(req: CollectAlertContextRequest) -> dict:
    return normalize_alert_input(req).model_dump(mode="json")


@app.post("/tools/resolve_primary_target", response_model=InvestigationTarget)
def resolve_target(req: InvestigationReportRequest) -> InvestigationTarget:
    return resolve_primary_target_from_request(req)


@app.post("/tools/build_investigation_plan", response_model=InvestigationPlan)
def build_plan(req: BuildInvestigationPlanRequest) -> InvestigationPlan:
    return build_investigation_plan_from_request(req)


@app.post("/tools/execute_investigation_step", response_model=EvidenceBatchExecution)
def execute_plan_step(req: ExecuteInvestigationStepRequest) -> EvidenceBatchExecution:
    return execute_investigation_step_from_request(req)


@app.post("/tools/update_investigation_plan", response_model=InvestigationPlan)
def update_plan(req: UpdateInvestigationPlanRequest) -> InvestigationPlan:
    return update_investigation_plan_from_request(req)


@app.post("/tools/find_unhealthy_workloads", response_model=UnhealthyWorkloadsResponse)
def find_unhealthy(req: FindUnhealthyWorkloadsRequest) -> UnhealthyWorkloadsResponse:
    return find_unhealthy_workloads(req)


@app.post("/tools/find_unhealthy_pod", response_model=UnhealthyPodResponse)
def find_unhealthy_single(req: FindUnhealthyPodRequest) -> UnhealthyPodResponse:
    return find_unhealthy_pod(req)


@app.post("/tools/rank_hypotheses", response_model=InvestigationAnalysis)
def rank_analysis(req: InvestigationReportRequest) -> InvestigationAnalysis:
    return rank_hypotheses_from_request(req)


@app.post("/tools/render_investigation_report", response_model=InvestigationReport)
def render_report(req: InvestigationReportRequest) -> InvestigationReport:
    return render_investigation_report(req)


@app.post("/tools/collect_change_candidates", response_model=CorrelatedChangesResponse)
def collect_change_candidates_route(req: CollectCorrelatedChangesRequest) -> CorrelatedChangesResponse:
    return collect_change_candidates(req)
