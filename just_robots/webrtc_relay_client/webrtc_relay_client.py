# NOTE: not sure why im getting import warnings, this is working.
# We"re pinned to a very specific version of aiortc, 1.9. 1.11 doesn"t work.
# 1.13 or higher has conflicts with v0.9 of forked aioice. This should be resolved at some point
import asyncio
import json
import logging
import typing as t
from uuid import UUID

import go2_robot_sdk.infrastructure.webrtc.go2_message_parsers as go2_parsers
import httpx
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCDataChannel,
    RTCPeerConnection,
    RTCSessionDescription,
)
from fastapi import status
from go2_robot_sdk.application.utils import command_generator
from go2_robot_sdk.domain.constants.robot_commands import ROBOT_CMD
from go2_robot_sdk.domain.constants.webrtc_topics import RTC_TOPIC
from go2_robot_sdk.domain.entities.robot_data import RobotData
from go2_robot_sdk.infrastructure.webrtc.data_decoder import WebRTCDataDecoder

import just_robots.webrtc_relay.webrtc_relay_types as wrt
from just_robots.fastapi_utils.fastapi_exceptions import StateException, raise_if_error
from just_robots.webrtc_relay.webrtc_relay_types import GetSubscriptionsReply
from just_robots.webrtc_relay_client.auth_token_provider import (
    FirebaseAuthTokenProvider,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


class WebRTCRelayClient:
    def __init__(
        self,
        relay_url: str,
        go2_ip_address: str,
        on_robot_data: t.Callable[[RobotData], t.Coroutine[None, None, None]],
        on_video_track: t.Callable[[MediaStreamTrack], t.Coroutine[None, None, None]],
        on_lidar_frame: t.Callable[[dict[str, t.Any]], t.Coroutine[None, None, None]],
        auth_provider: FirebaseAuthTokenProvider,
        go2_token: str = "",
    ):
        self.url = relay_url
        self.go2_ip_address = go2_ip_address
        self.go2_token = go2_token
        self._auth_provider = auth_provider
        self._client = httpx.AsyncClient(timeout=60.0)
        self._on_robot_data = on_robot_data
        self._on_video_track = on_video_track
        self._on_lidar_frame = on_lidar_frame
        self._data_decoder = WebRTCDataDecoder(enable_lidar_decoding=True)
        self._peer_connection = None
        self._peer_datachannel = None
        self._connection_id: UUID | None = None  # Set after start_relay
        self._shutdown_requested = False  # Flag to signal shutdown

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.shutdown()

    async def shutdown(self):
        """Fully shutdown client, disconnecting from GO2 and closing all connections."""
        logger.info("Shutting down WebRTC relay client...")

        # Set shutdown flag to break the infinite loop
        self._shutdown_requested = True

        # Close peer connection
        if self._peer_connection:
            try:
                logger.info("Closing peer connection...")
                await self._peer_connection.close()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Error closing peer connection during shutdown: {e}")
            finally:
                self._peer_connection = None

        # Close data channel (if still open)
        if self._peer_datachannel:
            try:
                logger.info("Closing peer data channel...")
                if self._peer_datachannel.readyState == "open":
                    await asyncio.to_thread(self._peer_datachannel.close)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Error closing peer data channel during shutdown: {e}")
            finally:
                self._peer_datachannel = None

        # Close HTTP client
        try:
            await self._client.aclose()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Error closing HTTP client during shutdown: {e}")

        logger.info("WebRTC relay client shutdown complete")

    async def connect_to_go2(self):
        await self._connect_to_go2()

    async def disconnect_from_go2(self):
        await self._disconnect_from_go2()

    async def start_relay(
        self, rtc_configuration: RTCConfiguration | None = None
    ) -> None:
        logger.info(f"establishing WebRTC connection to webrtc relay server")

        peer = RTCPeerConnection(configuration=rtc_configuration)
        peer.on(
            "connectionstatechange",
            lambda: logger.info(
                f"webrtc relay client peer connection {peer.connectionState=}"
            ),
        )
        peer.on("track", self._on_peer_track)
        _ = peer.addTransceiver("video", direction="recvonly")

        # Out-of-band data channel: both sides create with same id and negotiated=True
        data_channel = peer.createDataChannel("data", negotiated=True, id=0)
        data_channel.on(
            "open",
            lambda: logger.info(f"Client data channel is now open"),
        )
        data_channel.on("message", self._on_peer_datachannel_message)
        self._peer_datachannel = data_channel

        # Create offer (no trickle)
        peer_offer = await peer.createOffer()
        await peer.setLocalDescription(peer_offer)
        await self._wait_for_ice_gathering_complete(peer)

        peer_offer_args = wrt.OfferArgs(
            offer_sdp=peer.localDescription.sdp, offer_type=peer.localDescription.type
        )
        logger.info(
            f"sending webrtc connection offer to webrtc relay server. {peer_offer_args=}"
        )
        resp = await self._client.post(
            f"{self.url}/webrtc/offer",
            json=peer_offer_args.model_dump(mode="json"),
            headers=await self._get_auth_headers(),
        )
        if resp.status_code != status.HTTP_200_OK:
            err_json = resp.json()
            logger.warning(f"webrtc relay client offer failed. {err_json=}")
            raise_if_error(err_json)

        answer = wrt.OfferReply.model_validate(resp.json())
        self._connection_id = answer.connection_id
        logger.info(
            f"received answer from webrtc relay server. connection_id={self._connection_id}. "
            f"Connection established, waiting for data and video channels."
        )
        await peer.setRemoteDescription(
            RTCSessionDescription(sdp=answer.offer_sdp, type=answer.offer_type)
        )
        self._peer_connection = peer

    async def change_obstacle_avoid_state(self, enabled: bool):
        """robot sits down on hind legs (like a real dog would)"""
        if self._peer_datachannel is None:
            raise StateException(
                "call start before calling change_obstacle_avoid_state"
            )

        self._peer_datachannel.send(
            command_generator.gen_command(
                cmd=1001,
                parameters={"enabled": enabled},
                topic=RTC_TOPIC["OBSTACLE_AVOID"],
            )
        )

    async def move(
        self, forward_velocity: float, strafe_velocity: float, rotation_velocity: float
    ):
        """set the robot velocities. Must be sent frequently to maintain velocity, otherwise robot
        will stop moving. If the frequency is too low, there will be janky movement
        """
        if self._peer_datachannel is None:
            raise StateException("call start before calling move")

        self._peer_datachannel.send(
            command_generator.gen_mov_command(
                x=forward_velocity,
                y=strafe_velocity,
                z=rotation_velocity,
                obstacle_avoidance=False,
            )
        )

    async def gaze(self, roll_angle: float, pitch_angle: float, yaw_angle: float):
        """causes the robot to look towards the specified angles. This will not cause the
        robot to move its feet. 0,0,0 is looking forward
        """
        if self._peer_datachannel is None:
            raise StateException("call start before calling gaze")

        self._peer_datachannel.send(
            command_generator.gen_command(
                cmd=ROBOT_CMD["Euler"],
                parameters={"x": roll_angle, "y": pitch_angle, "z": yaw_angle},
                topic=RTC_TOPIC["SPORT_MOD"],
            )
        )

    async def stand_up(self):
        """causes the robot to stand up if it"s sitting. Does nothing if it"s already standing"""
        if self._peer_datachannel is None:
            raise StateException("call start before calling stand_up")

        self._peer_datachannel.send(
            command_generator.gen_command(
                cmd=ROBOT_CMD["StandUp"],
                parameters=None,
                topic=RTC_TOPIC["SPORT_MOD"],
            )
        )

    async def lie_down_on_belly(self):
        """robot slowly folds legs in to rest on its belly. This is the smoothest way to de-load the
        motors in prep for turning the robot off"""
        if self._peer_datachannel is None:
            raise StateException("call start before calling lie_down_on_belly")

        self._peer_datachannel.send(
            command_generator.gen_command(
                cmd=ROBOT_CMD["StandDown"],
                parameters=None,
                topic=RTC_TOPIC["SPORT_MOD"],
            )
        )

    async def sit_on_hind_legs(self):
        """robot sits down on hind legs (like a real dog would)"""
        if self._peer_datachannel is None:
            raise StateException("call start before calling sit_on_hind_legs")

        self._peer_datachannel.send(
            command_generator.gen_command(
                cmd=ROBOT_CMD["Sit"],
                parameters=None,
                topic=RTC_TOPIC["SPORT_MOD"],
            )
        )

    async def stand_up_from(self):
        """robot stands up from sitting position (gets up from SIT command)"""
        if self._peer_datachannel is None:
            raise StateException("call start before calling stand_up_from")

        self._peer_datachannel.send(
            command_generator.gen_command(
                cmd=ROBOT_CMD["RiseSit"],
                parameters=None,
                topic=RTC_TOPIC["SPORT_MOD"],
            )
        )

    async def recovery_stand(self):
        """Recovery stand - robot stands up from any position"""
        if self._peer_datachannel is None:
            raise StateException("call start before calling recovery_stand")

        self._peer_datachannel.send(
            command_generator.gen_command(
                cmd=ROBOT_CMD["RecoveryStand"],
                parameters=None,
                topic=RTC_TOPIC["SPORT_MOD"],
            )
        )

    async def balance_stand(self):
        """Robot performs balance stand (api_id: 1002)"""
        if self._peer_datachannel is None:
            raise StateException("call start before calling balance_stand")

        self._peer_datachannel.send(
            command_generator.gen_command(
                cmd=1002,  # BALANCE_STAND command ID from raw_commands.md
                parameters=None,
                topic=RTC_TOPIC["SPORT_MOD"],
            )
        )

    async def stop_move(self):
        """Send STOPMOVE command to stop robot movement (api_id: 1003)"""
        if self._peer_datachannel is None:
            raise StateException("call start before calling stop_move")

        self._peer_datachannel.send(
            command_generator.gen_command(
                cmd=1003,  # STOPMOVE command ID from raw_commands.md
                parameters=None,
                topic=RTC_TOPIC["SPORT_MOD"],
            )
        )

    async def send_raw_json_command_direct_to_go2(
        self, command_str: str, try_to_validate: bool = True
    ) -> None:
        """
        Send a raw JSON command to the robot.

        Args:
            command_str: JSON command as a valid JSON string
                (e.g., "{"type": "msg", "topic": "...", ...}")

        Raises:
            StateException: If peer datachannel is not initialized
            ValueError: If command format is invalid
            json.JSONDecodeError: If command string is invalid JSON

        Usage:
            command_str = "{"type": "msg", "topic": "rt/api/sport/request", "data": {"header": {"identity": {"id": 12345, "api_id": 1004}}, "parameter": ""}}"
        """
        if self._peer_datachannel is None:
            raise StateException("call start before calling send_json_command")

        # Validate it"s valid JSON
        if try_to_validate:
            try:
                cmd_dict = json.loads(command_str)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON string: {e}") from e

            # Validate command structure
            try:
                # Check required fields
                if not isinstance(cmd_dict, dict):
                    raise ValueError("Command must be a dictionary/JSON object")  # noqa: TRY004
                if "type" not in cmd_dict:
                    raise ValueError("Command missing 'type' field")
                if "topic" not in cmd_dict:
                    raise ValueError("Command missing 'topic' field")
                if "data" not in cmd_dict:
                    raise ValueError("Command missing 'data' field")
                if "header" not in cmd_dict["data"]:
                    raise ValueError("Command missing 'data.header' field")
                if "identity" not in cmd_dict["data"]["header"]:
                    raise ValueError("Command missing 'data.header.identity' field")
                if "api_id" not in cmd_dict["data"]["header"]["identity"]:
                    raise ValueError(
                        "Command missing 'data.header.identity.api_id' field"
                    )
            except (KeyError, TypeError) as e:
                raise ValueError(f"Invalid command structure: {e}") from e

        # Send the command JSON string
        self._peer_datachannel.send(command_str)

    async def get_subscriptions(self) -> list[str]:
        """
        Get the list of topics subscribed to for this connection.
        Requires start_relay() to have been called first.
        Returns empty set if no topics are subscribed.
        """
        if self._connection_id is None:
            raise StateException("call start_relay before getting subscriptions")

        r = await self._client.get(
            f"{self.url}/go2/subscriptions",
            params={"connection_id": str(self._connection_id)},
            headers=await self._get_auth_headers(),
        )
        raise_if_error(r)

        return GetSubscriptionsReply.model_validate(r.json()).subscribed_topics

    async def add_topic_to_subscriptions(self, topic: str):
        """
        Add a topic to the list of topics subscribed to.
        Requires start_relay() to have been called first.
        """
        if not topic:
            raise ValueError("topic cannot be empty")
        if self._connection_id is None:
            raise StateException("call start_relay before adding subscriptions")

        r = await self._client.post(
            f"{self.url}/go2/add-subscription",
            json=wrt.AddSubscriptionArgs(
                connection_id=self._connection_id, topic=topic
            ).model_dump(mode="json"),
            headers=await self._get_auth_headers(),
        )
        raise_if_error(r)

    async def remove_topic_from_subscriptions(self, topic: str):
        """
        Remove a topic from the list of topics subscribed to.
        Requires start_relay() to have been called first.
        """
        if self._connection_id is None:
            raise StateException("call start_relay before removing subscriptions")

        r = await self._client.post(
            f"{self.url}/go2/remove-subscription",
            json=wrt.RemoveSubscriptionArgs(
                connection_id=self._connection_id, topic=topic
            ).model_dump(mode="json"),
            headers=await self._get_auth_headers(),
        )
        raise_if_error(r)

    async def _get_auth_headers(self) -> dict[str, str]:
        """Get Authorization headers with ID token from auth provider."""
        id_token = await self._auth_provider.get_id_token()
        return {"Authorization": f"Bearer {id_token}"}

    async def _connect_to_go2(self):
        logger.info(
            f"instructing webrtc relay server to connect to the go2 at {self.go2_ip_address=}"
        )

        connect_args = wrt.ConnectArgs(
            robot_ip=self.go2_ip_address,
            robot_num=1,  # TODO (swapnil) - pipe this properly
            token=self.go2_token,
        )

        connect_reply = await self._client.post(
            f"{self.url}/go2/connect",
            json=connect_args.model_dump(mode="json"),
            headers=await self._get_auth_headers(),
        )

        raise_if_error(connect_reply)
        logger.info(
            f"webrtc server reported successful connection to go2. {connect_reply.json()}"
        )

    async def _disconnect_from_go2(self):
        try:
            r = await self._client.post(
                f"{self.url}/go2/disconnect",
                json={},  # Send empty JSON body as required by FastAPI for Pydantic model parameter
                headers=await self._get_auth_headers(),
            )
            raise_if_error(r)
            logger.info(f"[client] /disconnect: {r.json()}")
        except Exception as e:  # noqa: BLE001
            logger.info(f"[client] /disconnect failed: {e}")

    async def _on_peer_datachannel(self, data_channel: RTCDataChannel):
        logger.info(f"received datachannel from webrtc relay server. {data_channel=}")

        data_channel.on(
            "open",
            lambda *_: logger.info(
                f"datachannel to webrtc relay server is now open. {data_channel=}"
            ),
        )
        data_channel.on("message", self._on_peer_datachannel_message)
        self._peer_datachannel = data_channel

    async def _on_peer_datachannel_message(self, data: bytes | str | t.Any):
        try:
            if isinstance(data, bytes):
                logger.debug("got lidar data")
                lidar_frame = await asyncio.to_thread(
                    self._data_decoder.decode_array_buffer, data
                )
                if lidar_frame is None:
                    logger.warning(f"failed to decode binary message from data_channel")
                    return

                await self._on_lidar_frame(lidar_frame)
                return

            elif isinstance(data, str):  # noqa: RET505
                ret = go2_parsers.parse_datachannel_message(data)
                robot_data = go2_parsers.process_webrtc_message(ret, "0")
                if robot_data:
                    await self._on_robot_data(robot_data)

                return

            else:
                logger.warning(
                    f"got unexpected data type from webrtc relay: {type(data)!s}, {data=}"
                )
                return

        except BaseException as exception:  # noqa: BLE001
            logger.warning(
                f"got exception while trying to parse message from webrtc relay data channel. {exception=}"
            )

    async def _on_peer_track(self, track: MediaStreamTrack):
        logger.info(f"received video track from webrtc relay server. {track=}")
        if track.kind != "video":
            logger.info(f"track type was not video, ignoring")
            return

        await self._on_video_track(track)

    async def _wait_for_ice_gathering_complete(self, pc: RTCPeerConnection):
        if pc.iceGatheringState == "complete":
            return
        done = asyncio.get_event_loop().create_future()

        def check_state():
            if pc.iceGatheringState == "complete" and not done.done():
                done.set_result(True)

        pc.add_listener("icegatheringstatechange", check_state)
        check_state()
        await done
