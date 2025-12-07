from __future__ import annotations
import typing as t


import json
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from arcana_go2.fastapi_client import HTTPClient, JSONObject
from arcana_go2.logger import make_logger

lg = make_logger(__name__)

_COMMAND_ENDPOINT = "/api/webrtc"

class CommandResponse(BaseModel):
    id: int
    topic: str
    api_id: int
    parameter: str
    priority: int


class ArcanaGO2Base:
    def __init__(
        self,
        *,
        base_url: str,
        get_token: t.Callable[[], str | None] | None = None,
        timeout: float = 15.0,
    ) -> None:
        lg.debug(f"constructing client for {base_url=}")
        self._client = HTTPClient(
            base_url=base_url,
            get_token=get_token,
            timeout=timeout,
        )

    async def __aenter__(self) -> ArcanaGO2Base:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self):
        lg.debug("cleaning up go2 driver base")
        await self._client.close()

    async def send_command(
        self,
        *,
        requester_id: int,
        topic: str,
        api_id: int,
        command_args: JSONObject | None,
        priority: Literal[0, 1] = 0,
    ) -> CommandResponse | None:
        payload: dict[str, t.Any] = {
            "id": requester_id,
            "topic": topic,
            "api_id": api_id,
            "parameter": (
                "" if command_args is None else json.dumps(command_args, separators=(",", ":"))
            ),
            "priority": int(priority),
        }
        lg.debug(f"sending {payload=} to endpoint={_COMMAND_ENDPOINT}")
        return await self._client.post(_COMMAND_ENDPOINT, model=CommandResponse, payload=payload)
