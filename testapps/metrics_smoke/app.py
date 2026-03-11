import os
import time
from enum import Enum
import json
import urllib.error
import urllib.request

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Histogram, generate_latest


class MetricsScenario(str, Enum):
    HEALTHY_COMPLETE = "healthy_complete"
    WEAK_BUT_USABLE = "weak_but_usable"
    EMPTY_OR_BROKEN = "empty_or_broken"
    LOKI_COMPLEMENTARY = "loki_complementary"


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
LOKI_PUSH_URL = os.getenv("LOKI_PUSH_URL", "").strip()
ENABLE_LOKI_DIRECT_PUSH = os.getenv("ENABLE_LOKI_DIRECT_PUSH", "").strip().lower() in {"1", "true", "yes", "on"}
POD_NAMESPACE = os.getenv("POD_NAMESPACE", "metrics-smoke").strip() or "metrics-smoke"
POD_NAME = os.getenv("POD_NAME", "metrics-api").strip() or "metrics-api"
SERVICE_NAME = os.getenv("SERVICE_NAME", "metrics-api").strip() or "metrics-api"

app = FastAPI()


def metrics_registry_for_scenario(
    scenario: MetricsScenario,
    *,
    now_monotonic: float | None = None,
) -> CollectorRegistry:
    if scenario == MetricsScenario.HEALTHY_COMPLETE:
        return SERVICE_METRICS_REGISTRY
    if scenario == MetricsScenario.LOKI_COMPLEMENTARY:
        return SERVICE_METRICS_REGISTRY
    if scenario == MetricsScenario.EMPTY_OR_BROKEN:
        return EMPTY_METRICS_REGISTRY

    now_monotonic = now_monotonic if now_monotonic is not None else time.monotonic()
    if now_monotonic - APP_START_MONOTONIC < HIDE_AFTER_SECONDS:
        return SERVICE_METRICS_REGISTRY
    return EMPTY_METRICS_REGISTRY


def loki_push_enabled_for_scenario(scenario: MetricsScenario) -> bool:
    return scenario == MetricsScenario.LOKI_COMPLEMENTARY and ENABLE_LOKI_DIRECT_PUSH and bool(LOKI_PUSH_URL)


def build_loki_push_payload(line: str, *, timestamp_ns: int | None = None) -> bytes:
    timestamp_ns = timestamp_ns if timestamp_ns is not None else time.time_ns()
    payload = {
        "streams": [
            {
                "stream": {
                    "job": "metrics-smoke",
                    "namespace": POD_NAMESPACE,
                    "pod": POD_NAME,
                    "service": SERVICE_NAME,
                },
                "values": [[str(timestamp_ns), line]],
            }
        ]
    }
    return json.dumps(payload).encode("utf-8")


def maybe_push_loki_log(line: str) -> bool:
    if not loki_push_enabled_for_scenario(SCENARIO):
        return False
    request = urllib.request.Request(
        LOKI_PUSH_URL,
        data=build_loki_push_payload(line),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError):
        return False


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
    print("exception: synthetic upstream timeout", flush=True)
    maybe_push_loki_log("exception: synthetic upstream timeout")
    return {"status": "slow"}


@app.get("/fail")
def fail():
    print("error: upstream returned 500", flush=True)
    maybe_push_loki_log("error: upstream returned 500")
    return Response(content='{"status":"fail"}', status_code=500, media_type="application/json")


@app.get("/metrics")
def metrics():
    registry = metrics_registry_for_scenario(SCENARIO)
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
