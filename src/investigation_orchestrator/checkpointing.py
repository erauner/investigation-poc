from dataclasses import dataclass

from langgraph.checkpoint.memory import InMemorySaver

@dataclass(frozen=True)
class GraphCheckpointConfig:
    thread_id: str | None = None
    checkpoint_ns: str | None = None
    checkpoint_id: str | None = None


def build_graph_config(
    checkpoint_config: GraphCheckpointConfig,
) -> dict[str, dict[str, str]]:
    if not checkpoint_config.thread_id:
        raise ValueError("checkpoint_config.thread_id is required when checkpointing is enabled")

    configurable = {"thread_id": checkpoint_config.thread_id}
    if checkpoint_config.checkpoint_ns:
        configurable["checkpoint_ns"] = checkpoint_config.checkpoint_ns
    if checkpoint_config.checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_config.checkpoint_id
    return {"configurable": configurable}


def create_in_memory_checkpointer() -> InMemorySaver:
    return InMemorySaver()
