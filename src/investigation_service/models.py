from typing import Literal

from pydantic import BaseModel, Field

ProfileType = Literal["workload", "service", "otel-pipeline"]
ScopeType = Literal["workload", "service", "node", "otel-pipeline"]
ConfidenceType = Literal["low", "medium", "high"]


class TargetRef(BaseModel):
    namespace: str | None = None
    kind: Literal["pod", "deployment", "service", "node"]
    name: str


class CollectContextRequest(BaseModel):
    namespace: str | None = Field(default=None, description="Kubernetes namespace when target is namespaced")
    target: str = Field(..., description="Target in form pod/name, deployment/name, service/name, or plain name")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    service_name: str | None = Field(default=None, description="Optional service name hint for service profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")


class CollectAlertContextRequest(BaseModel):
    alertname: str = Field(..., description="Alert name")
    labels: dict[str, str] = Field(default_factory=dict, description="Alert labels")
    annotations: dict[str, str] = Field(default_factory=dict, description="Alert annotations")
    namespace: str | None = Field(default=None, description="Optional namespace override")
    node_name: str | None = Field(default=None, description="Optional node override for cluster-scoped node alerts")
    target: str | None = Field(default=None, description="Optional target override")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    service_name: str | None = Field(default=None, description="Optional service name hint for service profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")


class NormalizedInvestigationRequest(BaseModel):
    source: Literal["manual", "alert"]
    scope: ScopeType
    namespace: str | None = Field(default=None, description="Namespace for namespaced targets")
    target: str = Field(..., description="Normalized target in kind/name form")
    node_name: str | None = Field(default=None, description="Explicit node target when scope=node")
    service_name: str | None = Field(default=None, description="Explicit service target when scope=service")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")
    normalization_notes: list[str] = Field(default_factory=list)


class CollectNodeContextRequest(BaseModel):
    node_name: str = Field(..., description="Cluster node name")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")


class CollectServiceContextRequest(BaseModel):
    namespace: str = Field(..., description="Kubernetes namespace")
    service_name: str = Field(..., description="Kubernetes service name")
    target: str | None = Field(default=None, description="Optional explicit target override")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")


class FindUnhealthyWorkloadsRequest(BaseModel):
    namespace: str = Field(..., description="Kubernetes namespace")
    limit: int = Field(default=5, ge=1, le=20, description="Maximum number of unhealthy workloads to return")


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
    enrichment_hints: list[str] = Field(default_factory=list)


class UnhealthyWorkloadCandidate(BaseModel):
    target: str
    namespace: str
    kind: Literal["pod"]
    name: str
    phase: str | None = None
    reason: str | None = None
    restart_count: int = 0
    ready: bool = False
    summary: str


class UnhealthyWorkloadsResponse(BaseModel):
    namespace: str
    candidates: list[UnhealthyWorkloadCandidate]
    limitations: list[str] = Field(default_factory=list)


class RootCauseReport(BaseModel):
    scope: ScopeType
    target: str
    diagnosis: str
    likely_cause: str | None = None
    confidence: ConfidenceType
    evidence: list[str]
    limitations: list[str]
    recommended_next_step: str
    suggested_follow_ups: list[str] = Field(default_factory=list)


class InvestigationResponse(BaseModel):
    diagnosis: str
    evidence: list[str]
    recommendation: str
