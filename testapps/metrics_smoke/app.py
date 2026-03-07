import time

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest

app = FastAPI()

REQUEST_LATENCY = Histogram(
    "http_server_request_duration_seconds",
    "Request latency in seconds",
    ["method", "route", "status"],
)


@app.middleware("http")
async def observe_latency(request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration = time.monotonic() - start

    if request.url.path != "/metrics":
        REQUEST_LATENCY.labels(
            method=request.method,
            route=request.url.path,
            status=str(response.status_code),
        ).observe(duration)

    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ok")
def ok():
    return {"status": "ok"}


@app.get("/slow")
def slow():
    time.sleep(1.6)
    return {"status": "slow"}


@app.get("/fail")
def fail():
    return Response(content='{"status":"fail"}', status_code=500, media_type="application/json")


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
