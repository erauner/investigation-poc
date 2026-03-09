#!/usr/bin/env python3
from investigation_adk_agent.evidence_collectors import InternalSurrogateWorkloadCollector
from investigation_adk_agent.orchestrator import run_alert_canary
from investigation_service.models import BuildInvestigationPlanRequest


def main() -> None:
    incident = BuildInvestigationPlanRequest(
        cluster="current-context",
        namespace="operator-smoke",
        target="pod/crashy",
        profile="workload",
        alertname="PodCrashLooping",
        labels={"namespace": "operator-smoke", "pod": "crashy"},
        annotations={"summary": "CrashLooping pod crashy in operator-smoke"},
    )
    result = run_alert_canary(
        incident,
        collector=InternalSurrogateWorkloadCollector(),
    )
    print(result.markdown)


if __name__ == "__main__":
    main()
