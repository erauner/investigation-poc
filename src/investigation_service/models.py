from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

ProfileType = Literal["workload", "service", "otel-pipeline"]
ScopeType = Literal["workload", "service", "node", "otel-pipeline"]
InvestigationMode = Literal["generic", "alert"]
ConfidenceType = Literal["low", "medium", "high"]
GuidelineCategory = Literal["interpretation", "data_source", "next_step", "delegation", "safety"]


class TargetRef(BaseModel):
    namespace: str | None = None
    kind: Literal["pod", "deployment", "service", "node"]
    name: str


class CollectContextRequest(BaseModel):
    cluster: str | None = Field(default=None, description="Logical cluster alias")
    namespace: str | None = Field(default=None, description="Kubernetes namespace when target is namespaced")
    target: str = Field(..., description="Target in form pod/name, deployment/name, service/name, or plain name")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    service_name: str | None = Field(default=None, description="Optional service name hint for service profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")


class CollectAlertContextRequest(BaseModel):
    alertname: str = Field(..., description="Alert name")
    labels: dict[str, str] = Field(default_factory=dict, description="Alert labels")
    annotations: dict[str, str] = Field(default_factory=dict, description="Alert annotations")
    cluster: str | None = Field(default=None, description="Logical cluster alias")
    namespace: str | None = Field(default=None, description="Optional namespace override")
    node_name: str | None = Field(default=None, description="Optional node override for cluster-scoped node alerts")
    target: str | None = Field(default=None, description="Optional target override")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    service_name: str | None = Field(default=None, description="Optional service name hint for service profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")


class NormalizedInvestigationRequest(BaseModel):
    source: Literal["manual", "alert"]
    scope: ScopeType
    cluster: str | None = Field(default=None, description="Resolved logical cluster alias")
    namespace: str | None = Field(default=None, description="Namespace for namespaced targets")
    target: str = Field(..., description="Normalized target in kind/name form")
    node_name: str | None = Field(default=None, description="Explicit node target when scope=node")
    service_name: str | None = Field(default=None, description="Explicit service target when scope=service")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")
    normalization_notes: list[str] = Field(default_factory=list)


class InvestigationTarget(BaseModel):
    source: Literal["manual", "alert"]
    scope: ScopeType
    cluster: str | None = Field(default=None, description="Resolved logical cluster alias")
    namespace: str | None = Field(default=None, description="Namespace for namespaced targets")
    requested_target: str = Field(..., description="Original requested or inferred logical target")
    target: str = Field(..., description="Current canonical target in kind/name form")
    node_name: str | None = Field(default=None, description="Explicit node target when scope=node")
    service_name: str | None = Field(default=None, description="Explicit service target when scope=service")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")
    normalization_notes: list[str] = Field(default_factory=list)


class EvidenceBundle(BaseModel):
    cluster: str = "current-context"
    target: TargetRef
    object_state: dict
    events: list[str]
    log_excerpt: str
    metrics: dict
    findings: list["Finding"]
    limitations: list[str] = Field(default_factory=list)
    enrichment_hints: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class PlannedInvestigation:
    mode: InvestigationMode
    target: InvestigationTarget
    evidence: "EvidenceBundle"
    normalized: NormalizedInvestigationRequest
    context: Any


class CollectNodeContextRequest(BaseModel):
    cluster: str | None = Field(default=None, description="Logical cluster alias")
    node_name: str = Field(..., description="Cluster node name")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")


class CollectServiceContextRequest(BaseModel):
    cluster: str | None = Field(default=None, description="Logical cluster alias")
    namespace: str = Field(..., description="Kubernetes namespace")
    service_name: str = Field(..., description="Kubernetes service name")
    target: str | None = Field(default=None, description="Optional explicit target override")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")


class FindUnhealthyWorkloadsRequest(BaseModel):
    cluster: str | None = Field(default=None, description="Logical cluster alias")
    namespace: str = Field(..., description="Kubernetes namespace")
    limit: int = Field(default=5, ge=1, le=20, description="Maximum number of unhealthy workloads to return")


class FindUnhealthyPodRequest(BaseModel):
    cluster: str | None = Field(default=None, description="Logical cluster alias")
    namespace: str = Field(..., description="Kubernetes namespace")


class InvestigateRequest(BaseModel):
    cluster: str | None = Field(default=None, description="Logical cluster alias")
    namespace: str = Field(..., description="Kubernetes namespace")
    target: str = Field(..., description="Target in form pod/name, deployment/name, service/name, or plain name")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    service_name: str | None = Field(default=None, description="Optional service name hint for service profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")


class BuildRootCauseReportRequest(BaseModel):
    cluster: str | None = Field(default=None, description="Logical cluster alias")
    namespace: str | None = Field(default=None, description="Namespace for namespaced targets")
    target: str = Field(..., description="Target in form pod/name, deployment/name, service/name, or node/name")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    service_name: str | None = Field(default=None, description="Optional service name hint for service profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")


class CollectCorrelatedChangesRequest(BaseModel):
    cluster: str | None = Field(default=None, description="Logical cluster alias")
    namespace: str | None = Field(default=None, description="Namespace for namespaced targets")
    target: str = Field(..., description="Target in form pod/name, deployment/name, service/name, or node/name")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    service_name: str | None = Field(default=None, description="Optional service name hint for service profile")
    lookback_minutes: int = Field(default=60, ge=1, le=1440, description="Correlation window in minutes")
    anchor_timestamp: str | None = Field(default=None, description="Optional timestamp to anchor the correlation window")
    limit: int = Field(default=10, ge=1, le=25, description="Maximum correlated changes to return")


class InvestigationReportRequest(BaseModel):
    cluster: str | None = Field(default=None, description="Logical cluster alias")
    namespace: str | None = Field(default=None, description="Namespace for namespaced targets")
    target: str | None = Field(default=None, description="Target in form pod/name, deployment/name, service/name, or node/name")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    service_name: str | None = Field(default=None, description="Optional service name hint for service profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")
    include_related_data: bool = Field(default=True, description="Whether to collect correlated changes")
    correlation_window_minutes: int = Field(default=60, ge=1, le=1440, description="Correlation window in minutes")
    correlation_limit: int = Field(default=10, ge=1, le=25, description="Maximum correlated changes to return")
    anchor_timestamp: str | None = Field(default=None, description="Optional timestamp to anchor the correlation window")
    alertname: str | None = Field(default=None, description="Optional alert name for alert-shaped investigation input")
    labels: dict[str, str] = Field(default_factory=dict, description="Optional alert labels")
    annotations: dict[str, str] = Field(default_factory=dict, description="Optional alert annotations")
    node_name: str | None = Field(default=None, description="Optional node override for alert-shaped node investigations")


class AlertInvestigationReportRequest(BaseModel):
    alertname: str = Field(..., description="Alert name")
    labels: dict[str, str] = Field(default_factory=dict, description="Alert labels")
    annotations: dict[str, str] = Field(default_factory=dict, description="Alert annotations")
    cluster: str | None = Field(default=None, description="Logical cluster alias")
    namespace: str | None = Field(default=None, description="Optional namespace override")
    node_name: str | None = Field(default=None, description="Optional node override for node-scoped alerts")
    target: str | None = Field(default=None, description="Optional target override")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    service_name: str | None = Field(default=None, description="Optional service name hint for service profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")
    include_related_data: bool = Field(default=True, description="Whether to collect correlated changes")
    correlation_window_minutes: int = Field(default=60, ge=1, le=1440, description="Correlation window in minutes")
    correlation_limit: int = Field(default=10, ge=1, le=25, description="Maximum correlated changes to return")
    anchor_timestamp: str | None = Field(default=None, description="Optional timestamp to anchor the correlation window")


class GuidelineMatch(BaseModel):
    scope: ScopeType | None = None
    alertname: str | None = None
    namespace: str | None = None
    service_name: str | None = None
    target_kind: str | None = None
    target_name: str | None = None
    diagnosis: str | None = None
    cluster: str | None = None
    confidence: ConfidenceType | None = None


class GuidelineAction(BaseModel):
    category: GuidelineCategory
    text: str
    agent: str | None = None
    source: str | None = None


class GuidelineRule(BaseModel):
    id: str
    priority: int = Field(default=100, ge=0, le=1000)
    match: GuidelineMatch = Field(default_factory=GuidelineMatch)
    actions: list[GuidelineAction] = Field(default_factory=list)


class ResolvedGuideline(BaseModel):
    id: str
    category: GuidelineCategory
    text: str
    matched_on: list[str] = Field(default_factory=list)
    priority: int
    source: str | None = None
    agent: str | None = None


class GuidelineContext(BaseModel):
    cluster: str | None = "current-context"
    scope: ScopeType
    target: str
    target_kind: str | None = None
    target_name: str | None = None
    diagnosis: str
    confidence: ConfidenceType
    alertname: str | None = None
    namespace: str | None = None
    service_name: str | None = None


class Finding(BaseModel):
    severity: Literal["info", "warning", "critical"]
    source: Literal["k8s", "events", "logs", "prometheus", "heuristic"]
    title: str
    evidence: str


class EvidenceItem(BaseModel):
    fingerprint: str
    source: Literal["k8s", "events", "logs", "prometheus", "heuristic"]
    kind: Literal["finding", "event", "metric", "object_state"]
    severity: Literal["info", "warning", "critical"]
    summary: str
    detail: str | None = None


class CollectedContextResponse(BaseModel):
    cluster: str = "current-context"
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
    cluster: str = "current-context"
    namespace: str
    candidates: list[UnhealthyWorkloadCandidate]
    limitations: list[str] = Field(default_factory=list)


class UnhealthyPodResponse(BaseModel):
    cluster: str = "current-context"
    namespace: str
    candidate: UnhealthyWorkloadCandidate | None = None
    limitations: list[str] = Field(default_factory=list)


class RootCauseReport(BaseModel):
    cluster: str = "current-context"
    scope: ScopeType
    target: str
    diagnosis: str
    likely_cause: str | None = None
    confidence: ConfidenceType
    evidence: list[str]
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    limitations: list[str]
    recommended_next_step: str
    suggested_follow_ups: list[str] = Field(default_factory=list)


class Hypothesis(BaseModel):
    key: str
    diagnosis: str
    likely_cause: str | None = None
    confidence: ConfidenceType
    score: int
    supporting_findings: list[Finding] = Field(default_factory=list)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)


class InvestigationAnalysis(BaseModel):
    cluster: str = "current-context"
    scope: ScopeType
    target: str
    profile: ProfileType
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    recommended_next_step: str
    suggested_follow_ups: list[str] = Field(default_factory=list)


class CorrelatedChange(BaseModel):
    fingerprint: str
    timestamp: str
    source: Literal["k8s_event", "rollout", "config_change", "argocd", "prometheus_rule"]
    resource_kind: str
    namespace: str | None = None
    name: str
    relation: Literal["direct", "same_workload", "same_service", "same_node", "namespace", "cluster"]
    summary: str
    confidence: ConfidenceType


class CorrelatedChangesResponse(BaseModel):
    cluster: str = "current-context"
    scope: ScopeType
    target: str
    changes: list[CorrelatedChange]
    limitations: list[str] = Field(default_factory=list)


class InvestigationReport(BaseModel):
    cluster: str = "current-context"
    scope: ScopeType
    target: str
    diagnosis: str
    likely_cause: str | None = None
    confidence: ConfidenceType
    evidence: list[str]
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    related_data: list[CorrelatedChange] = Field(default_factory=list)
    related_data_note: str | None = None
    limitations: list[str] = Field(default_factory=list)
    recommended_next_step: str
    suggested_follow_ups: list[str] = Field(default_factory=list)
    guidelines: list[ResolvedGuideline] = Field(default_factory=list)
    normalization_notes: list[str] = Field(default_factory=list)


class InvestigationResponse(BaseModel):
    diagnosis: str
    evidence: list[str]
    recommendation: str
