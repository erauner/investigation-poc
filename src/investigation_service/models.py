from typing import Literal

from pydantic import BaseModel, Field


class TargetRef(BaseModel):
    namespace: str
    kind: Literal["pod", "deployment", "service"]
    name: str


class CollectContextRequest(BaseModel):
    namespace: str = Field(..., description="Kubernetes namespace")
    target: str = Field(..., description="Target in form pod/name, deployment/name, service/name, or plain name")


class InvestigateRequest(BaseModel):
    namespace: str = Field(..., description="Kubernetes namespace")
    target: str = Field(..., description="Target in form pod/name, deployment/name, service/name, or plain name")


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


class InvestigationResponse(BaseModel):
    diagnosis: str
    evidence: list[str]
    recommendation: str
