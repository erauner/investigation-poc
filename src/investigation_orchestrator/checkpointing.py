from dataclasses import dataclass
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

@dataclass(frozen=True)
class GraphCheckpointConfig:
    thread_id: str | None = None
    checkpoint_ns: str | None = None
    checkpoint_id: str | None = None


def build_graph_config(
    checkpoint_config: GraphCheckpointConfig | None = None,
) -> dict[str, dict[str, str]]:
    checkpoint_config = checkpoint_config or GraphCheckpointConfig()
    configurable = {
        "thread_id": checkpoint_config.thread_id or str(uuid4()),
    }
    if checkpoint_config.checkpoint_ns:
        configurable["checkpoint_ns"] = checkpoint_config.checkpoint_ns
    if checkpoint_config.checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_config.checkpoint_id
    return {"configurable": configurable}


def create_in_memory_checkpointer() -> BaseCheckpointSaver:
    return InMemorySaver()
