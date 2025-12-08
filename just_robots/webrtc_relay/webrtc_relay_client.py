# NOTE: not sure why im getting import warnings, this is working.
# We"re pinned to a very specific version of aiortc, 1.9. 1.11 doesn"t work.
# 1.13 or higher has conflicts with v0.9 of forked aioice. This should be resolved at some point
import argparse
import asyncio
import base64
import contextlib
import json
import logging
import os
import sys
import typing as t
from pathlib import Path

import go2_robot_sdk.infrastructure.webrtc.go2_message_parsers as go2_parsers
import httpx
import keyboard
from aiortc import (
    MediaStreamTrack,  # type: ignore
    RTCConfiguration,
    RTCDataChannel,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from go2_robot_sdk.application.utils import command_generator
from go2_robot_sdk.domain.constants.robot_commands import ROBOT_CMD
from go2_robot_sdk.domain.constants.webrtc_topics import RTC_TOPIC
from go2_robot_sdk.domain.entities.robot_config import RobotConfig
from go2_robot_sdk.domain.entities.robot_data import RobotData
from go2_robot_sdk.infrastructure.webrtc.data_decoder import WebRTCDataDecoder

import just_robots.webrtc_relay.voxel_map_viewer as vmv
from just_robots.firebase.firebase_client import FirebaseAuthManager
from just_robots.webrtc_relay.ice_server_config import get_ice_servers_list, get_rtc_configuration
from just_robots.webrtc_relay.keyboard_command_handler import (
    KeyboardCommandHandler,  # pyright: ignore[reportUnusedImport]
    TerminalInputAdapter,
)
from just_robots.webrtc_relay.webrtc_relay_client_video_viewer import display_video
from just_robots.webrtc_relay.webrtc_relay_endpoint_go2 import ConnectArgs
from just_robots.webrtc_relay.webrtc_relay_endpoint_webrtc import OfferArgs, OfferReply
from just_robots.webrtc_relay.webrtc_relay_exceptions import (  # pyright: ignore[reportUnusedImport]
    StateException,
    raise_if_error,
)
from just_robots.webrtc_relay.webrtc_stats_monitor import WebRTCStatsMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

KEYBOARD_AVAILABLE = True

TOPICS_TO_SUBSCRIBE_TO = {
    # RTC_TOPIC["MULTIPLE_STATE"],
    # RTC_TOPIC["SPORT_MOD_STATE"],
    # RTC_TOPIC["LOW_STATE"],
    # RTC_TOPIC["ULIDAR"],
    # RTC_TOPIC["ULIDAR_ARRAY"],
    # RTC_TOPIC["ULIDAR_STATE"],
    RTC_TOPIC["ROBOTODOM"],
}


class WebRTCRelayClient:
    def __init__(
        self,
        relay_url: str,
        robot_config: RobotConfig,
        on_robot_data: t.Callable[[RobotData], t.Coroutine[None, None, None]],
        on_video_track: t.Callable[[MediaStreamTrack], t.Coroutine[None, None, None]],
        on_lidar_frame: t.Callable[[dict[str, t.Any]], t.Coroutine[None, None, None]],
        topics_to_subscribe_to: set[str] | None = None,
        firebase_auth_manager: FirebaseAuthManager | None = None,
    ):
        self.url = relay_url
        self.robot_config = robot_config
        self._firebase_auth_manager = firebase_auth_manager
        self._client = httpx.AsyncClient(timeout=60.0)
        self._on_robot_data = on_robot_data
        self._on_video_track = on_video_track
        self._on_lidar_frame = on_lidar_frame
        self._data_decoder = WebRTCDataDecoder(enable_lidar_decoding=True)
        self._peer_connection = None
        self._peer_datachannel = None
        self._stats_monitor: WebRTCStatsMonitor | None = None
        self._topics_to_subscribe_to: set[str] = (
            topics_to_subscribe_to if topics_to_subscribe_to is not None else TOPICS_TO_SUBSCRIBE_TO
        )
        self._shutdown_requested = False  # Flag to signal shutdown

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.shutdown()

    def _get_auth_headers(self) -> dict[str, str]:
        """Get HTTP headers with Firebase authentication token."""
        if self._firebase_auth_manager:
            return self._firebase_auth_manager.get_auth_headers()
        return {}

    async def shutdown(self):
        """Fully shutdown client, disconnecting from GO2 and closing all connections."""
        logger.info("Shutting down WebRTC relay client...")

        # Set shutdown flag to break the infinite loop
        self._shutdown_requested = True

        # Stop stats monitoring
        if self._stats_monitor:
            await self._stats_monitor.stop()
            self._stats_monitor = None

        # Disconnect from GO2 via relay server
        try:
            await self._disconnect_from_go2()
        except Exception as e:
            logger.warning(f"Error disconnecting from GO2 during shutdown: {e}")

        # Close peer connection
        if self._peer_connection:
            try:
                logger.info("Closing peer connection...")
                await self._peer_connection.close()
            except Exception as e:
                logger.warning(f"Error closing peer connection during shutdown: {e}")
            finally:
                self._peer_connection = None

        # Close data channel (if still open)
        if self._peer_datachannel:
            try:
                logger.info("Closing peer data channel...")
                if self._peer_datachannel.readyState == "open":
                    await asyncio.to_thread(self._peer_datachannel.close)
            except Exception as e:
                logger.warning(f"Error closing peer data channel during shutdown: {e}")
            finally:
                self._peer_datachannel = None

        # Close HTTP client
        with contextlib.suppress(Exception):
            await self._client.aclose()

        logger.info("WebRTC relay client shutdown complete")

    async def start(self, connect_go2: bool = True):
        logger.debug("webrtc relay client start")
        if connect_go2:
            await self._connect_to_go2()

        self._peer_connection, self._peer_datachannel = await self._create_peer_connection()

    async def change_obstacle_avoid_state(self, enabled: bool):
        """robot sits down on hind legs (like a real dog would)"""
        if self._peer_datachannel is None:
            raise StateException("call start before calling change_obstacle_avoid_state")

        self._peer_datachannel.send(
            command_generator.gen_command(
                cmd=1001,
                parameters={"enabled": enabled},
                topic=RTC_TOPIC["OBSTACLE_AVOID"],
            )
        )

    async def move(self, forward_velocity: float, strafe_velocity: float, rotation_velocity: float):
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

    async def send_json_command(self, command_str: str, try_to_validate: bool = True) -> None:
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
                raise ValueError(f"Invalid JSON string: {e}")

            # Validate command structure
            try:
                # Check required fields
                if not isinstance(cmd_dict, dict):
                    raise ValueError("Command must be a dictionary/JSON object")
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
                    raise ValueError("Command missing 'data.header.identity.api_id' field")
            except (KeyError, TypeError) as e:
                raise ValueError(f"Invalid command structure: {e}")

        # Send the command JSON string
        self._peer_datachannel.send(command_str)

    async def _connect_to_go2(self):
        logger.info(
            f"instructing webrtc relay server to connect to the go2 at {self.robot_config=}"
        )

        # Build ConnectArgs - if topics_to_subscribe_to is None, don"t pass it
        # The server will use its default (TOPICS_TO_SUBSCRIBE_TO)
        connect_args = ConnectArgs(
            robot_ip=self.robot_config.robot_ip_list[0],
            robot_num=1,  # TODO (swapnil) - pipe this properly
            token=self.robot_config.token,
        )

        connect_args.topics_to_subscribe_to = list(self._topics_to_subscribe_to)
        logger.info(f"Connecting with custom topics: {self._topics_to_subscribe_to}")

        r = await self._client.post(
            f"{self.url}/go2/connect",
            json=connect_args.model_dump(),
            headers=self._get_auth_headers(),
        )

        raise_if_error(r)
        logger.info(f"webrtc server reported successful connection to go2. {r.json()}")

        # Sync client state with server subscriptions
        try:
            r = await self._client.get(
                f"{self.url}/go2/subscriptions", headers=self._get_auth_headers()
            )
            if r.status_code == 200:
                data = r.json()
                server_topics = set(data.get("subscribed_topics", set()))
                self._topics_to_subscribe_to = server_topics
                logger.info(f"Synced client subscriptions with server: {server_topics}")
        except Exception as e:
            logger.warning(f"Could not sync subscriptions: {e}")

    async def _disconnect_from_go2(self):
        try:
            r = await self._client.post(
                f"{self.url}/go2/disconnect",
                json={},  # Send empty JSON body as required by FastAPI for Pydantic model parameter
                headers=self._get_auth_headers(),
            )
            raise_if_error(r)
            logger.info("[client] /disconnect:", r.json())
        except Exception as e:
            logger.info("[client] /disconnect failed:", e)

    async def get_subscriptions(self) -> set[str]:
        """
        Get the list of topics subscribed to.
        Returns empty list if no topics are set.
        """
        # Try to fetch from server

        r = await self._client.get(
            f"{self.url}/go2/subscriptions", headers=self._get_auth_headers()
        )
        raise_if_error(r)

        data = r.json()
        self._topics_to_subscribe_to = set(data.get("subscribed_topics", set()))
        return self._topics_to_subscribe_to

    async def add_topic_to_subscriptions(self, topic: str):
        """
        Add a topic to the list of topics subscribed to.
        """
        if not topic:
            raise ValueError("topic cannot be empty")

        if topic in self._topics_to_subscribe_to:
            return

        updated_topics = self._topics_to_subscribe_to.union([topic])
        await self._update_subscriptions_go2(updated_topics)

    async def remove_topic_from_subscriptions(self, topic: str):
        """
        Remove a topic from the list of topics subscribed to.
        """
        if topic not in self._topics_to_subscribe_to:
            return

        temp = set(self._topics_to_subscribe_to)
        temp.remove(topic)
        await self._update_subscriptions_go2(temp)

    async def _update_subscriptions_go2(self, topics: set[str]):
        """
        Update the topics subscribed to from the GO2 robot without reconnecting.

        Args:
            topics: List of topic strings to subscribe to
        """
        logger.info(f"Updating subscription topics to: {topics=}")

        r = await self._client.post(
            f"{self.url}/go2/update-subscriptions",
            json={"topics": list(topics)},
            headers=self._get_auth_headers(),
        )
        raise_if_error(r)

        self._topics_to_subscribe_to = topics
        logger.info("Successfully updated topic subscriptions")

    async def _create_peer_connection(self) -> tuple[RTCPeerConnection, RTCDataChannel]:
        logger.info(f"establishing WebRTC connection to webrtc relay server")

        # Get ICE server configuration from environment variables
        rtc_config = get_rtc_configuration()
        ice_servers = get_ice_servers_list()

        # Safely extract URLs for logging
        ice_server_urls = []
        for s in ice_servers:
            try:
                if isinstance(s, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
                    url = s.get("urls", "unknown")
                    # Handle case where urls might be a list
                    if isinstance(url, list):
                        url = url[0] if url else "unknown"
                else:
                    # Handle case where it might be an object with urls attribute
                    url = getattr(s, "urls", "unknown")
                ice_server_urls.append(str(url))
            except Exception as e:
                logger.warning(f"Error extracting ICE server URL: {e}, server: {s}")
                ice_server_urls.append("unknown")
        logger.info(f"Using ICE servers: {ice_server_urls}")

        peer = RTCPeerConnection(configuration=rtc_config)
        peer.on(
            "connectionstatechange",
            lambda: logger.info(f"webrtc relay client peer connection {peer.connectionState=}"),
        )
        peer.on("track", self._on_peer_track)

        # Create the channel here so the OFFER includes m=application (SCTP)
        peer_datachannel = peer.createDataChannel("data")
        peer_datachannel.on("open", lambda: self._on_peer_datachannel_open(peer_datachannel))
        peer_datachannel.on("message", self._on_peer_datachannel_message)
        _peer_transceiver = peer.addTransceiver("video", direction="recvonly")

        # Create offer (no trickle)
        peer_offer = await peer.createOffer()
        await peer.setLocalDescription(peer_offer)
        await self._wait_for_ice_gathering_complete(peer)

        peer_offer_args = OfferArgs(sdp=peer.localDescription.sdp, type=peer.localDescription.type)
        logger.info(f"sending webrtc connection offer to webrtc relay server. {peer_offer_args=}")
        resp = await self._client.post(
            f"{self.url}/webrtc/offer",
            json=peer_offer_args.model_dump(),
            headers=self._get_auth_headers(),
        )
        if resp.status_code != 200:
            err_json = resp.json()
            logger.warning(f"webrtc relay client offer failed. {err_json=}")
            raise_if_error(err_json)

        answer = OfferReply.model_validate(resp.json())
        logger.info(
            f"received answer from webrtc relay server. {answer=}. "
            f"Connection established, waiting for data and video channels."
        )
        await peer.setRemoteDescription(RTCSessionDescription(sdp=answer.sdp, type=answer.type))

        # Start WebRTC stats monitoring for client→relay connection
        enable_stats = os.getenv("ENABLE_WEBRTC_STATS", "false").lower() in ("true", "1", "yes")
        debug_stats = os.getenv("DEBUG_WEBRTC_STATS", "false").lower() in ("true", "1", "yes")

        if enable_stats:
            self._stats_monitor = WebRTCStatsMonitor("CLIENT→RELAY", peer, debug=debug_stats)
            await self._stats_monitor.start(interval_seconds=5.0)
        else:
            logger.info("WebRTC stats monitoring disabled")
            self._stats_monitor = None

        return peer, peer_datachannel

    def _on_peer_datachannel_open(self, peer_connection_data_channel: RTCDataChannel):
        logger.info(
            f"datachannel to webrtc relay server is now open. {peer_connection_data_channel=}"
        )

    async def _on_peer_datachannel_message(self, data: bytes | str | t.Any):
        try:
            if isinstance(data, bytes):
                logger.debug("got lidar data")
                lidar_frame = await asyncio.to_thread(self._data_decoder.decode_array_buffer, data)
                if lidar_frame is None:
                    logger.warning(f"failed to decode binary message from data_channel")
                    return

                await self._on_lidar_frame(lidar_frame)
                return

            elif isinstance(data, str):
                ret = go2_parsers.parse_datachannel_message(data)
                robot_data = go2_parsers.process_webrtc_message(ret, "0")
                if robot_data:
                    await self._on_robot_data(robot_data)

                return

            else:
                logger.warning(
                    f"got unexpected data type from webrtc relay: {str(type(data))}, {data=}"
                )
                return

        except BaseException as exception:
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


async def keyboard_control_loop(client):
    """Read keyboard commands in real-time and send move commands using keyboard handler."""
    # Set client event loop reference for handler
    client._loop = asyncio.get_running_loop()

    # Create keyboard command handler and terminal adapter
    handler = KeyboardCommandHandler(client)
    _adapter = handler.create_terminal_adapter()

    # Get the event loop for thread-safe scheduling
    loop = asyncio.get_running_loop()

    # Build reverse mapping: terminal key -> action
    # Map both the config key and common variations
    terminal_key_to_action = {}
    for action, keys in handler.config["key_bindings"].items():
        if "terminal" in keys:
            terminal_key = keys["terminal"]
            # Map the key as-is
            terminal_key_to_action[terminal_key] = action
            # Also map lowercase version (keyboard library returns lowercase)
            terminal_key_to_action[terminal_key.lower()] = action
            # Handle special cases
            if terminal_key == " ":
                terminal_key_to_action["space"] = action
            elif terminal_key == "space":
                terminal_key_to_action[" "] = action

    # Track currently pressed keys
    pressed_keys: set[str] = set()
    quit_requested = False

    def on_key_event(event):
        """Handle key press/release events (called from keyboard library thread)."""
        # Schedule the actual handling on the event loop thread
        if event.event_type == keyboard.KEY_DOWN:
            key = event.name.lower()
            pressed_keys.add(key)

            # Handle quit
            if key == "q":
                nonlocal quit_requested
                quit_requested = True
                return

            # Get action for this key
            action = terminal_key_to_action.get(key)
            if action:
                # Handle stop action (synchronous, safe to call from any thread)
                if action == "stop":
                    handler.stop_movement()
                    return

                # Schedule key press handling on event loop
                # Pass the action directly to handler to avoid double conversion
                def handle_press():
                    handler.handle_key_press(action)

                loop.call_soon_threadsafe(handle_press)
        elif event.event_type == keyboard.KEY_UP:
            key = event.name.lower()
            pressed_keys.discard(key)

            # Get action for this key
            action = terminal_key_to_action.get(key)
            if action and action != "quit" and action != "stop":
                # Schedule key release handling on event loop
                # Pass the action, not the key, to the handler
                def handle_release():
                    handler.handle_key_release(action)

                loop.call_soon_threadsafe(handle_release)

    # Register keyboard hooks
    keyboard.hook(on_key_event)

    print("Keyboard control active (keys configured in keyboard_config.json)")
    print("Hold keys to move. Press 'q' to quit, space to stop")
    print("Note: Requires 'keyboard' library (pip install keyboard)")

    try:
        # Keep running until quit is requested
        while not quit_requested:
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        pass
    finally:
        # Cleanup
        keyboard.unhook_all()
        handler.stop_movement()
        print("Keyboard control stopped")


async def main(
    relay_url: str,
    config: RobotConfig,
    on_robot_data: t.Callable[[RobotData], t.Coroutine[None, None, None]],
    on_video_track: t.Callable[[MediaStreamTrack], t.Coroutine[None, None, None]],
    on_lidar_update: t.Callable[[dict[str, t.Any]], t.Coroutine[None, None, None]],
    topics_to_subscribe_to: set[str] | None = None,
    firebase_auth_manager: FirebaseAuthManager | None = None,
):
    async with WebRTCRelayClient(
        relay_url=str(relay_url),
        robot_config=config,
        on_video_track=on_video_track,
        on_lidar_frame=on_lidar_update,
        on_robot_data=on_robot_data,
        topics_to_subscribe_to=topics_to_subscribe_to,
        firebase_auth_manager=firebase_auth_manager,
    ) as client:
        logger.info("created webrtc relay client, calling start")
        await client.start(True)
        logger.info("created webrtc relay client, started")

        # start keyboard control loop
        keyboard_task = asyncio.create_task(keyboard_control_loop(client))
        try:
            await keyboard_task
        finally:
            keyboard_task.cancel()
            with contextlib.suppress(Exception):
                await keyboard_task

        while True:
            await asyncio.sleep(5)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Simple PC client for GO2 Pi bridge")
    p.add_argument("--api", default="http://localhost:8000", help="Pi bridge base URL")
    p.add_argument(
        "--robot-ip", default="192.168.12.1", help="GO2 AP IP (optional: call /connect first)"
    )
    p.add_argument("--robot-num", type=int, default=0)
    p.add_argument("--token", default="")
    p.add_argument(
        "--firebase-id-token",
        default=None,
        help="Firebase ID token for authentication (or set FIREBASE_ID_TOKEN env var)",
    )
    p.add_argument(
        "--firebase-config", default=None, help="Path to Firebase service account JSON file"
    )
    p.add_argument(
        "--firebase-api-key", default=None, help="Firebase API key for user authentication"
    )
    p.add_argument("--firebase-email", default=None, help="Firebase email for user authentication")
    p.add_argument(
        "--firebase-password", default=None, help="Firebase password for user authentication"
    )
    p.add_argument(
        "--dump-lidar",
        action="store_true",
        dest="dump_lidar",
        help="Write lidar frames to lidar_dump.txt",
    )
    p.add_argument(
        "--send-ping", action="store_true", help="Send a small bytes payload on datachannel open"
    )
    p.add_argument(
        "--disconnect-on-exit", default=True, action="store_true", help="Call /disconnect on exit"
    )
    args = p.parse_args()

    # Initialize Firebase authentication if provided
    firebase_auth_manager = None
    if args.firebase_id_token or args.firebase_config or args.firebase_api_key:
        firebase_auth_manager = FirebaseAuthManager(
            firebase_client_config_filepath=Path(),
            firebase_email=args.firebase_email,
            firebase_password=args.firebase_password,
        )
        if not firebase_auth_manager.is_authenticated():
            logger.warning("Firebase authentication configured but no valid token available")
        else:
            logger.info("Firebase authentication enabled")

    config = RobotConfig(
        robot_ip_list=[args.robot_ip],
        token=args.token,
        conn_type="webrtc",
        enable_video=True,
        decode_lidar=True,
        publish_raw_voxel=True,
        obstacle_avoidance=True,
        conn_mode="single",
    )

    # async def on_video_track(track: MediaStreamTrack):
    #     print(f"Video track received: {track}")

    # async def on_robot_data(robot_data: RobotData):
    #     print(f"Robot data received: {robot_data}")

    # asyncio.run(main(
    #     relay_url=args.api,
    #     config=config,
    #     on_robot_data=on_robot_data,
    #     on_video_track=on_video_track,
    #     on_lidar_update=on_lidar_update
    # ))

    display_task: asyncio.Task[None] | None = None
    try:

        async def on_video_track(track: MediaStreamTrack):
            logger.info(f"got video track: {track}")
            global display_task
            if display_task is not None:
                display_task.cancel()
                await display_task

            display_task = asyncio.create_task(display_video(track))

        async def on_lidar_update(lidar_frame: dict[str, t.Any]):
            print(
                f"Lidar frame received with {lidar_frame.get('decoded_data', {}).get('face_count', 0)} faces"
            )

        # vmv_viewer = vmv.VoxelMapViewer(flip_winding=False, compute_normals_every=1)
        # ff = open("lidar_dump.txt", mode="w+") if args.dump_lidar else None
        # num_writes = 0
        # try:
        #     vmv_viewer.start()
        #     async def on_lidar_update(lidar_frame: dict[str, t.Any]):
        #         global num_writes
        #         dec = lidar_frame["decoded_data"]
        #         meta = lidar_frame["data"]
        #         positions = dec["positions"]           # np.uint8, length = face_count*12
        #         face_count = int(dec["face_count"])
        #         vmv_viewer.submit_u8(
        #             positions_u8=positions,
        #             face_count=face_count,
        #             resolution=float(meta["resolution"]),
        #             origin_xyz=meta["origin"],
        #         )
        #         if ff and num_writes < 1000:
        #             num_writes += 1
        #             combined = lidar_frame["compressed_metadata"] + lidar_frame["compressed_data"]
        #             b64 = base64.b64encode(combined).decode("ascii")
        #             record = {"frame": b64}
        #             ff.write(json.dumps(record) + "\n")

        # New robot data hook
        # async def on_robot_data(robot_data):
        #     # logger.debug("on robot data")
        #     try:
        #         if robot_data and robot_data.odometry_data:
        #             odom = robot_data.odometry_data
        #             # vmv_viewer.submit_robot_pose(
        #             #     position=odom.position,         # {"x":..,"y":..,"z":..}
        #             #     orientation=odom.orientation,   # {"x":..,"y":..,"z":..,"w":..}
        #             # )
        #             logger.info("Odom position: %s", json.dumps(odom.position))
        #             logger.info("Odom orientation: %s", json.dumps(odom.orientation))
        #     except Exception as e:
        #         logger.warning(f"robot pose update failed: {e}")

        async def on_robot_data(robot_data: RobotData):
            logger.info(f"robot data received: {robot_data}")

        asyncio.run(
            main(
                relay_url=args.api,
                config=config,
                on_robot_data=on_robot_data,
                on_video_track=on_video_track,
                on_lidar_update=on_lidar_update,
                topics_to_subscribe_to=TOPICS_TO_SUBSCRIBE_TO,
                firebase_auth_manager=firebase_auth_manager,
            )
        )
        # finally:
        #     if ff:
        #         ff.close()
        #     vmv_viewer.close()
    finally:
        if display_task is not None:
            display_task.cancel()
            asyncio.wait_for(display_task, timeout=None)
