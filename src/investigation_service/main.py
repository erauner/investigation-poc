from fastapi import FastAPI

from .models import InvestigateRequest, InvestigationResponse
from .tools import get_events, get_k8s_objects, get_logs, query_prometheus

app = FastAPI(title="Investigation Service", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/investigate", response_model=InvestigationResponse)
def investigate(req: InvestigateRequest) -> InvestigationResponse:
    obj_state = get_k8s_objects(req.namespace, req.target)
    events = get_events(req.namespace, req.target)
    logs = get_logs(req.namespace, req.target)
    metrics = query_prometheus(req.namespace, req.target)

    evidence = [
        f"Object state: {obj_state}",
        f"Events: {events}",
        f"Metrics: {metrics}",
        f"Logs preview: {logs[:120]}",
    ]

    return InvestigationResponse(
        diagnosis="Investigation scaffold is running; no real diagnosis logic yet.",
        evidence=evidence,
        recommendation="Wire real kubernetes and prometheus clients, then add LLM reasoning.",
    )
