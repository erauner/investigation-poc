from pydantic import BaseModel, Field


class InvestigateRequest(BaseModel):
    namespace: str = Field(..., description="Kubernetes namespace")
    target: str = Field(..., description="Target workload or pod")


class InvestigationResponse(BaseModel):
    diagnosis: str
    evidence: list[str]
    recommendation: str
