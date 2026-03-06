from typing import Literal

from pydantic import BaseModel, Field

ProfileType = Literal["workload", "service", "otel-pipeline"]


class TargetRef(BaseModel):
    namespace: str
    kind: Literal["pod", "deployment", "service"]
    name: str


class CollectContextRequest(BaseModel):
    namespace: str = Field(..., description="Kubernetes namespace")
    target: str = Field(..., description="Target in form pod/name, deployment/name, service/name, or plain name")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    service_name: str | None = Field(default=None, description="Optional service name hint for service profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")


class CollectAlertContextRequest(BaseModel):
    alertname: str = Field(..., description="Alert name")
    labels: dict[str, str] = Field(default_factory=dict, description="Alert labels")
    annotations: dict[str, str] = Field(default_factory=dict, description="Alert annotations")
    namespace: str | None = Field(default=None, description="Optional namespace override")
    target: str | None = Field(default=None, description="Optional target override")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    service_name: str | None = Field(default=None, description="Optional service name hint for service profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")


class InvestigateRequest(BaseModel):
    namespace: str = Field(..., description="Kubernetes namespace")
    target: str = Field(..., description="Target in form pod/name, deployment/name, service/name, or plain name")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    service_name: str | None = Field(default=None, description="Optional service name hint for service profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")


class Finding(BaseModel):
    severity: Literal["info", "warning", "critical"]
    source: Literal["k8s", "events", "logs", "prometheus", "heuristic"]
    title: str
    evidence: str


class CollectedContextResponse(BaseModel):
    target: TargetRef
    object_state: dict
    events: list[str]
    log_excerpt: str
    metrics: dict
    findings: list[Finding]
    limitations: list[str] = Field(default_factory=list)


class InvestigationResponse(BaseModel):
    diagnosis: str
    evidence: list[str]
    recommendation: str
