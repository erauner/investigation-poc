from fastapi import FastAPI

from .models import CollectContextRequest, CollectedContextResponse, InvestigateRequest, InvestigationResponse
from .tools import collect_workload_context

app = FastAPI(title="Investigation Service", version="0.2.0")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/tools/collect_workload_context", response_model=CollectedContextResponse)
def collect_context(req: CollectContextRequest) -> CollectedContextResponse:
    return collect_workload_context(req)


@app.post("/investigate", response_model=InvestigationResponse)
def investigate(req: InvestigateRequest) -> InvestigationResponse:
    context = collect_workload_context(CollectContextRequest(namespace=req.namespace, target=req.target))

    critical = [f for f in context.findings if f.severity == "critical"]
    if critical:
        diagnosis = critical[0].title
        recommendation = "Inspect pod spec, recent events, and logs; restart only after root cause is confirmed."
    else:
        diagnosis = "No critical issue detected by deterministic checks."
        recommendation = "Continue with deeper service-level checks and confirm traffic/error trends."

    evidence = [
        f"Target: {context.target.kind}/{context.target.name} in namespace {context.target.namespace}",
        f"K8s object: {context.object_state}",
        f"Top findings: {[f.title for f in context.findings]}",
        f"Prometheus metrics: {context.metrics}",
    ]

    return InvestigationResponse(diagnosis=diagnosis, evidence=evidence, recommendation=recommendation)
