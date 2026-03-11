import os
import time
from enum import Enum

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Histogram, generate_latest


class MetricsScenario(str, Enum):
    HEALTHY_COMPLETE = "healthy_complete"
    WEAK_BUT_USABLE = "weak_but_usable"
    EMPTY_OR_BROKEN = "empty_or_broken"


def parse_metrics_scenario(value: str | None) -> MetricsScenario:
    raw = (value or MetricsScenario.HEALTHY_COMPLETE.value).strip().lower()
    try:
        return MetricsScenario(raw)
    except ValueError as exc:
        raise ValueError(f"unsupported METRICS_SCENARIO: {value}") from exc


SERVICE_METRICS_REGISTRY = CollectorRegistry()
EMPTY_METRICS_REGISTRY = CollectorRegistry()
REQUEST_LATENCY = Histogram(
    "http_server_request_duration_seconds",
    "Request latency in seconds",
    ["method", "route", "status"],
    registry=SERVICE_METRICS_REGISTRY,
)

SCENARIO = parse_metrics_scenario(os.getenv("METRICS_SCENARIO"))
APP_START_MONOTONIC = time.monotonic()
HIDE_AFTER_SECONDS = max(int(os.getenv("METRICS_HIDE_AFTER_SECONDS", "45")), 1)

app = FastAPI()


def metrics_registry_for_scenario(
    scenario: MetricsScenario,
    *,
    now_monotonic: float | None = None,
) -> CollectorRegistry:
    if scenario == MetricsScenario.HEALTHY_COMPLETE:
        return SERVICE_METRICS_REGISTRY
    if scenario == MetricsScenario.EMPTY_OR_BROKEN:
        return EMPTY_METRICS_REGISTRY

    now_monotonic = now_monotonic if now_monotonic is not None else time.monotonic()
    if now_monotonic - APP_START_MONOTONIC < HIDE_AFTER_SECONDS:
        return SERVICE_METRICS_REGISTRY
    return EMPTY_METRICS_REGISTRY


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
    return {"status": "ok", "scenario": SCENARIO.value}


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
    registry = metrics_registry_for_scenario(SCENARIO)
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
