from __future__ import annotations

import logging

import uvicorn

from .a2a_app import build_shadow_app
from .graph import graph
from .settings import get_shadow_agent_name, get_shadow_runtime_host, get_shadow_runtime_port


def _agent_card() -> dict:
    return {
        "name": get_shadow_agent_name(),
        "description": "Shadow BYO investigation runtime backed directly by the orchestrator library.",
        "url": "/",
        "version": "0.1.0",
        "capabilities": {"streaming": True},
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [
            {
                "id": "investigate-workload-shadow",
                "name": "Kubernetes Investigation Shadow",
                "description": "Run the shadow BYO investigation runtime and return a deterministic five-section report.",
                "tags": ["kubernetes", "investigation", "shadow"],
                "examples": [
                    "Investigate the unhealthy pod in namespace kagent-smoke. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.",
                    "Investigate Backend/crashy in namespace operator-smoke.",
                ],
            }
        ],
    }


def main() -> None:
    from kagent.core import KAgentConfig

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config = KAgentConfig()
    uvicorn.run(
        build_shadow_app(graph=graph, agent_card=_agent_card(), config=config, tracing=True),
        host=get_shadow_runtime_host(),
        port=get_shadow_runtime_port(),
        log_level="info",
    )


if __name__ == "__main__":
    main()
