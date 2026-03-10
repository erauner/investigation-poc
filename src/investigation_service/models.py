from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

ProfileType = Literal["workload", "service", "otel-pipeline"]
ScopeType = Literal["workload", "service", "node", "otel-pipeline"]
InvestigationMode = Literal["alert_rca", "targeted_rca", "factual_analysis"]
PlanStatus = Literal["pending", "completed", "deferred"]
ConfidenceType = Literal["low", "medium", "high"]
GuidelineCategory = Literal["interpretation", "data_source", "next_step", "delegation", "safety"]
HandoffStatus = Literal["awaiting_external_submission", "ready_for_next_handoff", "complete"]
HandoffNextAction = Literal["submit_external_steps", "call_handoff_again", "render_report"]


class TargetRef(BaseModel):
    namespace: str | None = None
    kind: Literal["pod", "deployment", "statefulset", "service", "node"]
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


class BuildInvestigationPlanRequest(BaseModel):
    cluster: str | None = Field(default=None, description="Logical cluster alias")
    namespace: str | None = Field(default=None, description="Namespace for namespaced targets")
    target: str | None = Field(default=None, description="Target in form pod/name, deployment/name, service/name, or node/name")
    profile: ProfileType = Field(default="workload", description="Investigation profile")
    service_name: str | None = Field(default=None, description="Optional service name hint for service profile")
    lookback_minutes: int = Field(default=15, ge=1, le=240, description="Metric lookback window in minutes")
    alertname: str | None = Field(default=None, description="Optional alert name for alert-shaped input")
    labels: dict[str, str] = Field(default_factory=dict, description="Optional alert labels")
    annotations: dict[str, str] = Field(default_factory=dict, description="Optional alert annotations")
    node_name: str | None = Field(default=None, description="Optional node override for alert-shaped node investigations")
    objective: Literal["auto", "rca", "factual"] = Field(
        default="auto",
        description="Planning objective. Use factual for capacity/inventory style analysis without RCA semantics.",
    )
    question: str | None = Field(default=None, description="Optional free-form planning question or objective")


class PlanStep(BaseModel):
    id: str
    title: str
    category: Literal["evidence", "analysis", "render", "summary"]
    plane: str
    status: PlanStatus = "pending"
    rationale: str
    suggested_capability: str | None = None
    preferred_mcp_server: str | None = None
    preferred_tool_names: list[str] = Field(default_factory=list)
    fallback_mcp_server: str | None = None
    fallback_tool_names: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class EvidenceBatch(BaseModel):
    id: str
    title: str
    status: PlanStatus = "pending"
    intent: str
    step_ids: list[str] = Field(default_factory=list)


class InvestigationPlan(BaseModel):
    mode: InvestigationMode
    objective: str
    target: InvestigationTarget | None = None
    steps: list[PlanStep] = Field(default_factory=list)
    evidence_batches: list[EvidenceBatch] = Field(default_factory=list)
    active_batch_id: str | None = None
    planning_notes: list[str] = Field(default_factory=list)


class InvestigationSubject(BaseModel):
    source: Literal["manual", "alert"]
    kind: Literal["target", "alert", "question"]
    summary: str
    requested_target: str | None = None
    alertname: str | None = None


class StepExecutionInputs(BaseModel):
    request_kind: Literal["alert_context", "target_context", "service_context", "change_candidates"]
    cluster: str | None = None
    namespace: str | None = None
    target: str | None = None
    profile: ProfileType | None = None
    service_name: str | None = None
    node_name: str | None = None
    lookback_minutes: int | None = None
    alertname: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    anchor_timestamp: str | None = None
    limit: int | None = None


class EvidenceStepContract(BaseModel):
    step_id: str
    title: str
    plane: str
    artifact_type: Literal["evidence_bundle", "change_candidates"]
    requested_capability: str | None = None
    preferred_mcp_server: str | None = None
    preferred_tool_names: list[str] = Field(default_factory=list)
    fallback_mcp_server: str | None = None
    fallback_tool_names: list[str] = Field(default_factory=list)
    execution_mode: Literal["external_preferred", "control_plane_only"]
    execution_inputs: StepExecutionInputs


class ActiveEvidenceBatchContract(BaseModel):
    batch_id: str
    title: str
    intent: str
    subject: InvestigationSubject
    canonical_target: InvestigationTarget | None = None
    steps: list[EvidenceStepContract] = Field(default_factory=list)


class ActualRoute(BaseModel):
    source_kind: Literal["investigation_internal", "peer_mcp"]
    mcp_server: str | None = None
    tool_name: str | None = None
    tool_path: list[str] = Field(default_factory=list)


class StepRouteProvenance(BaseModel):
    requested_capability: str | None = None
    route_satisfaction: Literal["preferred", "fallback", "unmatched", "not_applicable"] = "not_applicable"
    actual_route: ActualRoute


class ExecutedStepTrace(BaseModel):
    batch_id: str | None = None
    step_id: str
    plane: str
    artifact_type: Literal["evidence_bundle", "change_candidates"]
    provenance: StepRouteProvenance


class StepArtifact(BaseModel):
    step_id: str
    plane: str
    artifact_type: Literal["evidence_bundle", "change_candidates"]
    summary: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    evidence_bundle: "EvidenceBundle | None" = None
    change_candidates: "CorrelatedChangesResponse | None" = None
    route_provenance: StepRouteProvenance | None = None


class EvidenceBatchExecution(BaseModel):
    batch_id: str
    executed_step_ids: list[str] = Field(default_factory=list)
    artifacts: list[StepArtifact] = Field(default_factory=list)
    execution_notes: list[str] = Field(default_factory=list)


class ExecuteInvestigationStepRequest(BaseModel):
    plan: InvestigationPlan
    incident: BuildInvestigationPlanRequest
    batch_id: str | None = None


class UpdateInvestigationPlanRequest(BaseModel):
    plan: InvestigationPlan
    execution: EvidenceBatchExecution


class SubmittedStepArtifact(BaseModel):
    step_id: str
    evidence_bundle: "EvidenceBundle | None" = None
    change_candidates: "CorrelatedChangesResponse | None" = None
    actual_route: ActualRoute
    summary: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class GetActiveEvidenceBatchRequest(BaseModel):
    plan: InvestigationPlan
    incident: BuildInvestigationPlanRequest
    batch_id: str | None = None


class SubmitEvidenceArtifactsRequest(BaseModel):
    plan: InvestigationPlan
    incident: BuildInvestigationPlanRequest
    batch_id: str | None = None
    submitted_steps: list[SubmittedStepArtifact] = Field(default_factory=list)


class SubmittedEvidenceReconciliationResult(BaseModel):
    execution: EvidenceBatchExecution
    updated_plan: InvestigationPlan


class AdvanceInvestigationRuntimeRequest(BaseModel):
    incident: BuildInvestigationPlanRequest
    execution_context: "ReportingExecutionContext | None" = None
    batch_id: str | None = None
    submitted_steps: list[SubmittedStepArtifact] = Field(default_factory=list)


class AdvanceInvestigationRuntimeResponse(BaseModel):
    execution_context: "ReportingExecutionContext"
    next_active_batch: "ActiveEvidenceBatchContract | None" = None


class HandoffActiveEvidenceBatchRequest(BaseModel):
    incident: BuildInvestigationPlanRequest
    execution_context: "ReportingExecutionContext | None" = None
    handoff_token: str | None = None
    batch_id: str | None = None
    submitted_steps: list[SubmittedStepArtifact] = Field(default_factory=list)


class HandoffActiveEvidenceBatchResponse(BaseModel):
    execution_context: "ReportingExecutionContext"
    handoff_token: str
    active_batch: "ActiveEvidenceBatchContract | None" = None
    execution: EvidenceBatchExecution | None = None
    handoff_status: HandoffStatus = "complete"
    next_action: HandoffNextAction = "render_report"
    required_external_step_ids: list[str] = Field(default_factory=list)


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


class ReportingExecutionContext(BaseModel):
    updated_plan: InvestigationPlan
    executions: list[EvidenceBatchExecution] = Field(default_factory=list)
    initial_plan: InvestigationPlan | None = None
    allow_bounded_fallback_execution: bool = True


class InvestigationReportingRequest(InvestigationReportRequest):
    execution_context: ReportingExecutionContext | None = None


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


class ToolPathTrace(BaseModel):
    planner_path_used: bool = False
    source: str = "investigation-mcp-server"
    mode: InvestigationMode | None = None
    executed_batch_ids: list[str] = Field(default_factory=list)
    executed_step_ids: list[str] = Field(default_factory=list)
    step_provenance: list[ExecutedStepTrace] = Field(default_factory=list)


class InvestigationState(BaseModel):
    incident: BuildInvestigationPlanRequest
    target: InvestigationTarget | None = None
    plan: InvestigationPlan
    executions: list[EvidenceBatchExecution] = Field(default_factory=list)
    artifacts: list[StepArtifact] = Field(default_factory=list)
    primary_evidence: EvidenceBundle | None = None
    change_candidates: CorrelatedChangesResponse | None = None
    tool_path_trace: ToolPathTrace | None = None


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
    tool_path_trace: ToolPathTrace | None = None


class InvestigationResponse(BaseModel):
    diagnosis: str
    evidence: list[str]
    recommendation: str
