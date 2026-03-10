from __future__ import annotations

import base64
import json
import logging
import random
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any, cast

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    PendingWrite,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class KAgentCheckpointPayload(BaseModel):
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str
    parent_checkpoint_id: str | None = None
    checkpoint: str
    metadata: str
    type_: str
    version: int


class KAgentCheckpointWrite(BaseModel):
    idx: int
    channel: str
    type_: str
    value: str


class KAgentCheckpointWritePayload(BaseModel):
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str
    task_id: str
    writes: list[KAgentCheckpointWrite]


class KAgentCheckpointTuplePayload(BaseModel):
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str
    parent_checkpoint_id: str | None = None
    checkpoint: str
    metadata: str
    type_: str
    writes: KAgentCheckpointWritePayload | None = None


class KAgentCheckpointTupleResponse(BaseModel):
    data: list[KAgentCheckpointTuplePayload] | None = None


class ShadowKAgentCheckpointer(BaseCheckpointSaver[str]):
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        app_name: str,
        serde: SerializerProtocol | None = None,
    ):
        super().__init__(serde=serde)
        self.client = client
        self.app_name = app_name
        self.jsonplus_serde = JsonPlusSerializer()

    def _extract_config_values(self, config: RunnableConfig) -> tuple[str, str, str]:
        configurable = config.get("configurable", {})
        thread_id = configurable.get("thread_id")
        if not thread_id:
            raise ValueError("thread_id is required in config.configurable")
        user_id = configurable.get("user_id", "admin@kagent.dev")
        checkpoint_ns = configurable.get("checkpoint_ns", "")
        return thread_id, user_id, checkpoint_ns

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id, user_id, checkpoint_ns = self._extract_config_values(config)
        type_, serialized_checkpoint = self.serde.dumps_typed(checkpoint)
        serialized_metadata = json.dumps(get_checkpoint_metadata(config, metadata)).encode()
        response = await self.client.post(
            "/api/langgraph/checkpoints",
            json=KAgentCheckpointPayload(
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
                checkpoint_id=checkpoint["id"],
                parent_checkpoint_id=config.get("configurable", {}).get("checkpoint_id"),
                checkpoint=base64.b64encode(serialized_checkpoint).decode("ascii"),
                metadata=base64.b64encode(serialized_metadata).decode("ascii"),
                type_=type_,
                version=checkpoint["v"],
            ).model_dump(),
            headers={"X-User-ID": user_id},
        )
        response.raise_for_status()
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, user_id, checkpoint_ns = self._extract_config_values(config)
        checkpoint_id = config.get("configurable", {}).get("checkpoint_id")
        if not checkpoint_id:
            raise ValueError("checkpoint_id is required in config.configurable for writing checkpoint data")

        writes_payload = []
        for idx, (channel, value) in enumerate(writes):
            type_, serialized_value = self.serde.dumps_typed(value)
            writes_payload.append(
                KAgentCheckpointWrite(
                    idx=WRITES_IDX_MAP.get(channel, idx),
                    channel=channel,
                    type_=type_,
                    value=base64.b64encode(serialized_value).decode("ascii"),
                )
            )

        response = await self.client.post(
            "/api/langgraph/checkpoints/writes",
            json=KAgentCheckpointWritePayload(
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
                checkpoint_id=checkpoint_id,
                task_id=task_id,
                writes=writes_payload,
            ).model_dump(),
            headers={"X-User-ID": user_id},
        )
        response.raise_for_status()

    def _convert_to_checkpoint_tuple(
        self, config: RunnableConfig, checkpoint_tuple: KAgentCheckpointTuplePayload
    ) -> CheckpointTuple:
        return CheckpointTuple(
            config=config,
            checkpoint=self.serde.loads_typed(
                (checkpoint_tuple.type_, base64.b64decode(checkpoint_tuple.checkpoint.encode("ascii")))
            ),
            metadata=cast(
                CheckpointMetadata,
                json.loads(base64.b64decode(checkpoint_tuple.metadata.encode("ascii"))),
            ),
            parent_config=(
                {
                    "configurable": {
                        "thread_id": checkpoint_tuple.thread_id,
                        "checkpoint_ns": checkpoint_tuple.checkpoint_ns,
                        "checkpoint_id": checkpoint_tuple.parent_checkpoint_id,
                    }
                }
                if checkpoint_tuple.parent_checkpoint_id
                else None
            ),
            pending_writes=(
                [
                    PendingWrite(
                        (
                            checkpoint_tuple.writes.task_id,
                            write.channel,
                            self.serde.loads_typed((write.type_, base64.b64decode(write.value.encode("ascii")))),
                        )
                    )
                    for write in checkpoint_tuple.writes.writes
                ]
            )
            if checkpoint_tuple.writes
            else None,
        )

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id, user_id, checkpoint_ns = self._extract_config_values(config)
        params = {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "limit": "1"}
        checkpoint_id = get_checkpoint_id(config)
        if checkpoint_id:
            params["checkpoint_id"] = checkpoint_id
        response = await self.client.get(
            "/api/langgraph/checkpoints",
            params=params,
            headers={"X-User-ID": user_id},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = KAgentCheckpointTupleResponse.model_validate_json(response.text)
        if not data.data:
            return None
        checkpoint_tuple = data.data[0]
        if not checkpoint_id:
            config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_tuple.checkpoint_id,
                }
            }
        return self._convert_to_checkpoint_tuple(config, checkpoint_tuple)

    async def alist(
        self,
        config: RunnableConfig | None = None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if not config:
            raise ValueError("config is required")
        thread_id, user_id, checkpoint_ns = self._extract_config_values(config)
        response = await self.client.get(
            "/api/langgraph/checkpoints",
            params={
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "limit": str(limit if limit else -1),
            },
            headers={"X-User-ID": user_id},
        )
        response.raise_for_status()
        data = KAgentCheckpointTupleResponse.model_validate_json(response.text)
        if data.data:
            for checkpoint_tuple in data.data:
                tuple_config = dict(config)
                tuple_configurable = dict(config.get("configurable", {}))
                tuple_configurable.update(
                    {
                        "thread_id": checkpoint_tuple.thread_id,
                        "checkpoint_ns": checkpoint_tuple.checkpoint_ns,
                        "checkpoint_id": checkpoint_tuple.checkpoint_id,
                    }
                )
                tuple_config["configurable"] = tuple_configurable
                yield self._convert_to_checkpoint_tuple(tuple_config, checkpoint_tuple)

    def get_next_version(self, current: str | None, channel: None) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(current.split(".")[0])
        return f"{current_v + 1:032}.{random.random():016}"

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        raise NotImplementedError("Use async version (aput) instead")

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        raise NotImplementedError("Use async version (aput_writes) instead")

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        raise NotImplementedError("Use async version (aget_tuple) instead")

    def list(
        self,
        config: RunnableConfig | None = None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        raise NotImplementedError("Use async version (alist) instead")
