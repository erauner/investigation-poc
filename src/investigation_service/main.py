from fastapi import FastAPI

from .models import (
    CollectAlertContextRequest,
    CollectContextRequest,
    CollectNodeContextRequest,
    CollectServiceContextRequest,
    CollectedContextResponse,
    FindUnhealthyPodRequest,
    FindUnhealthyWorkloadsRequest,
    InvestigateRequest,
    InvestigationResponse,
    UnhealthyPodResponse,
    UnhealthyWorkloadsResponse,
)
from .tools import (
    build_root_cause_report,
    collect_alert_context,
    collect_node_context,
    collect_service_context,
    collect_workload_context,
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


@app.post("/tools/collect_node_context", response_model=CollectedContextResponse)
def collect_node(req: CollectNodeContextRequest) -> CollectedContextResponse:
    return collect_node_context(req)


@app.post("/tools/collect_service_context", response_model=CollectedContextResponse)
def collect_service(req: CollectServiceContextRequest) -> CollectedContextResponse:
    return collect_service_context(req)


@app.post("/tools/find_unhealthy_workloads", response_model=UnhealthyWorkloadsResponse)
def find_unhealthy(req: FindUnhealthyWorkloadsRequest) -> UnhealthyWorkloadsResponse:
    return find_unhealthy_workloads(req)


@app.post("/tools/find_unhealthy_pod", response_model=UnhealthyPodResponse)
def find_unhealthy_single(req: FindUnhealthyPodRequest) -> UnhealthyPodResponse:
    return find_unhealthy_pod(req)


@app.post("/investigate", response_model=InvestigationResponse)
def investigate(req: InvestigateRequest) -> InvestigationResponse:
    context = collect_workload_context(
        CollectContextRequest(
            namespace=req.namespace,
            target=req.target,
            profile=req.profile,
            service_name=req.service_name,
            lookback_minutes=req.lookback_minutes,
        )
    )
    report = build_root_cause_report(
        context,
        CollectContextRequest(
            namespace=req.namespace,
            target=req.target,
            profile=req.profile,
            service_name=req.service_name,
            lookback_minutes=req.lookback_minutes,
        ),
    )
    evidence = [
        f"Target: {context.target.kind}/{context.target.name} in namespace {context.target.namespace}",
        f"Profile: {req.profile}",
        *report.evidence,
        f"Prometheus metrics: {context.metrics}",
        f"Limitations: {context.limitations}",
    ]
    return InvestigationResponse(
        diagnosis=report.diagnosis,
        evidence=evidence,
        recommendation=report.recommended_next_step,
    )
