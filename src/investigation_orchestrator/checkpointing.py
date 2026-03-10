from dataclasses import dataclass

from langgraph.checkpoint.memory import InMemorySaver


@dataclass(frozen=True)
class GraphCheckpointConfig:
    thread_id: str | None = None
    checkpoint_ns: str | None = None
    checkpoint_id: str | None = None


def resolve_checkpoint_config(
    *,
    checkpoint_config: GraphCheckpointConfig | None = None,
    thread_id: str | None = None,
    checkpoint_ns: str | None = None,
    checkpoint_id: str | None = None,
    require_thread_id: bool = False,
) -> GraphCheckpointConfig | None:
    if checkpoint_config is None and thread_id is None and checkpoint_ns is None and checkpoint_id is None:
        if require_thread_id:
            raise ValueError("explicit thread_id is required when checkpointing is enabled")
        return None

    resolved = GraphCheckpointConfig(
        thread_id=thread_id if thread_id is not None else checkpoint_config.thread_id if checkpoint_config else None,
        checkpoint_ns=checkpoint_ns if checkpoint_ns is not None else checkpoint_config.checkpoint_ns if checkpoint_config else None,
        checkpoint_id=checkpoint_id if checkpoint_id is not None else checkpoint_config.checkpoint_id if checkpoint_config else None,
    )
    if require_thread_id and not resolved.thread_id:
        raise ValueError("explicit thread_id is required when checkpointing is enabled")
    return resolved


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
