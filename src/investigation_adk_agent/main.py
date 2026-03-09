import os

from .agent import DEFAULT_AGENT_CARD, configured_model_name, run_investigation
from .prompt import SYSTEM_INSTRUCTIONS


def create_app():
    try:
        from kagent.adk import KAgentApp
    except ImportError as exc:  # pragma: no cover - exercised via unit test
        raise RuntimeError(
            "kagent ADK runtime is not installed. Install the BYO ADK dependencies before launching the canary app."
        ) from exc

    try:
        from a2a.types import AgentCapabilities, AgentCard
        from google.adk.agents import LlmAgent
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:  # pragma: no cover - exercised via unit test
        raise RuntimeError(
            "Google ADK is not installed. Install the BYO ADK dependencies before launching the canary app."
        ) from exc

    def root_agent_factory():
        return LlmAgent(
            name=DEFAULT_AGENT_CARD.name,
            model=LiteLlm(model=configured_model_name()),
            description=DEFAULT_AGENT_CARD.description,
            instruction=SYSTEM_INSTRUCTIONS,
            tools=[run_investigation],
        )

    return KAgentApp(
        root_agent_factory=root_agent_factory,
        agent_card=AgentCard(
            name=DEFAULT_AGENT_CARD.name,
            description=DEFAULT_AGENT_CARD.description,
            version=DEFAULT_AGENT_CARD.version,
            url=os.getenv(
                "KAGENT_ADK_AGENT_URL",
                f"http://127.0.0.1:8083/api/a2a/kagent/{DEFAULT_AGENT_CARD.name}/",
            ),
            capabilities=AgentCapabilities(
                streaming=True,
                pushNotifications=False,
                stateTransitionHistory=True,
            ),
            defaultInputModes=["text"],
            defaultOutputModes=["text"],
            skills=[],
        ),
        kagent_url=os.getenv(
            "KAGENT_URL",
            "http://kagent-controller.kagent.svc.cluster.local:8083",
        ),
        app_name=DEFAULT_AGENT_CARD.name,
    )


app = create_app
