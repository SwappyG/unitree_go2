from __future__ import annotations


import typing as t

from arcana_go2.arcana_go2_base import ArcanaGO2Base, CommandResponse
from arcana_go2.fastapi_client import JSONObject
from arcana_go2.logger import make_logger

lg = make_logger(__name__)


_SPORT_TOPIC = "rt/api/sport/request"
_OBSTACLE_TOPIC = "rt/api/obstacles_avoid/request"


class ArcanaGO2:
    def __init__(self, *, base_url: str, get_token=None, timeout: float = 15.0) -> None:
        lg.debug("constructing up go2 driver")
        self._base = ArcanaGO2Base(base_url=base_url, get_token=get_token, timeout=timeout)

    async def __aenter__(self) -> "ArcanaGO2":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self):
        lg.debug("cleaning up go2 driver")
        await self._base.close()

    async def _send(
        self,
        *,
        requester_id: int,
        topic: str,
        api_id: int,
        command_args: JSONObject | None,
        priority: t.Literal[0, 1],
    ) -> CommandResponse | None:
        return await self._base.send_command(
            requester_id=requester_id,
            topic=topic,
            api_id=api_id,
            command_args=command_args,
            priority=priority,
        )

    async def damp(self, *, id: int, priority: t.Literal[0, 1] = 0) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1001, command_args=None, priority=priority
        )

    async def stopmove(self, *, id: int, priority: t.Literal[0, 1] = 0) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1003, command_args=None, priority=priority
        )

    async def standup(self, *, id: int, priority: t.Literal[0, 1] = 0) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1004, command_args=None, priority=priority
        )

    async def standdown(self, *, id: int, priority: t.Literal[0, 1] = 0) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1005, command_args=None, priority=priority
        )

    async def recoverystand(
        self, *, id: int, priority: t.Literal[0, 1] = 0
    ) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1006, command_args=None, priority=priority
        )

    async def euler(
        self, *, id: int, x: float, y: float, z: float, priority: t.Literal[0, 1] = 1
    ) -> CommandResponse | None:
        return await self._send(
            requester_id=id,
            topic=_SPORT_TOPIC,
            api_id=1007,
            command_args={"x": x, "y": y, "z": z},
            priority=priority,
        )

    async def move(
        self, *, id: int, x: float, y: float, z: float, priority: t.Literal[0, 1] = 1
    ) -> CommandResponse | None:
        return await self._send(
            requester_id=id,
            topic=_SPORT_TOPIC,
            api_id=1008,
            command_args={"x": x, "y": y, "z": z},
            priority=priority,
        )

    async def sit(self, *, id: int, priority: t.Literal[0, 1] = 0) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1009, command_args=None, priority=priority
        )

    async def risesit(self, *, id: int, priority: t.Literal[0, 1] = 0) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1010, command_args=None, priority=priority
        )

    async def speedlevel(
        self, *, id: int, data: int, priority: t.Literal[0, 1] = 0
    ) -> CommandResponse | None:
        return await self._send(
            requester_id=id,
            topic=_SPORT_TOPIC,
            api_id=1015,
            command_args={"data": data},
            priority=priority,
        )

    async def hello(self, *, id: int, priority: t.Literal[0, 1] = 0) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1016, command_args=None, priority=priority
        )

    async def stretch(self, *, id: int, priority: t.Literal[0, 1] = 0) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1017, command_args=None, priority=priority
        )

    async def content(self, *, id: int, priority: t.Literal[0, 1] = 0) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1020, command_args=None, priority=priority
        )

    async def dance1(self, *, id: int, priority: t.Literal[0, 1] = 0) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1022, command_args=None, priority=priority
        )

    async def dance2(self, *, id: int, priority: t.Literal[0, 1] = 0) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1023, command_args=None, priority=priority
        )

    async def switch_joystick(
        self, *, id: int, flag: bool, priority: t.Literal[0, 1] = 0
    ) -> CommandResponse | None:
        return await self._send(
            requester_id=id,
            topic=_SPORT_TOPIC,
            api_id=1027,
            command_args={"flag": flag},
            priority=priority,
        )

    async def pose(
        self, *, id: int, flag: bool, priority: t.Literal[0, 1] = 0
    ) -> CommandResponse | None:
        return await self._send(
            requester_id=id,
            topic=_SPORT_TOPIC,
            api_id=1028,
            command_args={"flag": flag},
            priority=priority,
        )

    async def frontjump(self, *, id: int, priority: t.Literal[0, 1] = 0) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1031, command_args=None, priority=priority
        )

    async def frontpounce(
        self, *, id: int, priority: t.Literal[0, 1] = 0
    ) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1032, command_args=None, priority=priority
        )

    async def staticwalk(self, *, id: int, priority: t.Literal[0, 1] = 0) -> CommandResponse | None:
        return await self._send(
            requester_id=id, topic=_SPORT_TOPIC, api_id=1061, command_args=None, priority=priority
        )

    async def handstand(
        self, *, id: int, flag: bool, priority: t.Literal[0, 1] = 0
    ) -> CommandResponse | None:
        return await self._send(
            requester_id=id,
            topic=_SPORT_TOPIC,
            api_id=2044,
            command_args={"flag": flag},
            priority=priority,
        )

    async def obstacle_avoid_switch_set(
        self, *, id: int, enable: bool, priority: t.Literal[0, 1] = 0
    ) -> CommandResponse | None:
        return await self._send(
            requester_id=id,
            topic=_OBSTACLE_TOPIC,
            api_id=1001,
            command_args={"enable": bool(enable)},
            priority=priority,
        )
