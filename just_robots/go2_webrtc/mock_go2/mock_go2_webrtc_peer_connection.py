import asyncio
import logging
import uuid
from types import TracebackType

from aiortc import MediaStreamTrack, RTCDataChannel, RTCPeerConnection
from aiortc.contrib.media import MediaBlackhole
from go2_robot_sdk.infrastructure.webrtc.crypto.encryption import ValidationCrypto

import just_robots.go2_webrtc.go2_message as go2m
from just_robots.fastapi_utils.fastapi_exceptions import StateException
from just_robots.go2_webrtc.go2_connection_messages import (
    GenericMessage,
    MessageMessage,
    SubscribeMessage,
    UnsubscribeMessage,
    ValidationMessage,
    VideoMessage,
)
from just_robots.go2_webrtc.mock_go2.mock_go2_video_track import MockGo2VideoTrack

logger = logging.getLogger(__name__)


class MockGo2WebRTCPeerConnection:
    def __init__(
        self,
        peer_connection: RTCPeerConnection,
        video_track: MockGo2VideoTrack,
        publish_freq: float = 10.0,
    ) -> None:
        self._pc_uuid = uuid.uuid4()
        self._publish_period = 1 / publish_freq

        self._is_validated = False
        self._is_dead = False
        self._pc = peer_connection
        self._pending_validation_key: str | None = None
        self._pc.on("datachannel", self._on_datachannel)
        self._pc.on("connectionstatechange", self._on_connection_state_change)
        self._pc.on("track", self._on_track)

        self._video_track = video_track
        self._video_track_enabled = False
        self._blackholes = MediaBlackhole()

        self._subscriptions: set[str] = set()
        self._enable_lidar_publishing = False
        self._pub_task: asyncio.Task[None] | None = None

        self._lidar_frame_cv = asyncio.Condition()
        self._lidar_frame: bytes | None = None

        self._lidar_pub_task: asyncio.Task[None] | None = None
        self._data_pub_task: asyncio.Task[None] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _tb: TracebackType | None,
    ):
        await self.stop()

    @property
    def pc_uuid(self) -> uuid.UUID:
        return self._pc_uuid

    @property
    def is_dead(self) -> bool:
        return self._is_dead

    async def stop(self) -> None:
        logger.info("stopping mock go2 webrtc peer connection")
        self._is_dead = True

        if self._lidar_pub_task:
            logger.info("cancelling lidar publish task")
            self._lidar_pub_task.cancel()

        if self._data_pub_task:
            logger.info("cancelling data publish task")
            self._data_pub_task.cancel()

        logger.info("stopping media blackholes")
        await self._blackholes.stop()

        logger.info("closing peer connection")
        await self._pc.close()

    async def queue_lidar_frame(self, lidar_frame: bytes) -> None:
        async with self._lidar_frame_cv:
            self._lidar_frame = bytes(lidar_frame)
            self._lidar_frame_cv.notify_all()

    async def _on_connection_state_change(self) -> None:
        logger.info(f"PC state: {self._pc.connectionState}")
        if self._pc.connectionState in ("failed", "closed"):
            await self.stop()

    async def _on_track(self, track: MediaStreamTrack) -> None:
        logger.info(f"client sent track {track.kind}")
        self._blackholes.addTrack(track)

    async def _on_datachannel(self, channel: RTCDataChannel) -> None:
        logger.info(f"datachannel: {channel.label}")

        # make channel accessible to the message handler
        async def on_message(message: str | bytes) -> None:
            await self._on_datachannel_message(channel, message)

        channel.on("message", on_message)

        key = uuid.uuid4().hex  # 32 hex chars
        self._pending_validation_key = key

        # Send the plaintext key to the client
        try:
            channel.send(ValidationMessage(data=key).model_dump_json())
            logger.info("Sent validation key to client")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to send validation key: {e}")

    async def _on_validation_message(
        self, channel: RTCDataChannel, message: ValidationMessage
    ) -> None:
        if self._is_validated:
            raise StateException("already validated")
        # Client is responding with encrypted key (or asking to start)
        if self._pending_validation_key is None:
            self._pending_validation_key = uuid.uuid4().hex

        expected = ValidationCrypto.encrypt_key(self._pending_validation_key)
        if message.data == expected:
            logger.info("validation accepted (encrypted key matched)")
            # Acknowledge exactly as specified
            try:
                channel.send(ValidationMessage.validation_ok().model_dump_json())
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to send validation ack: {e}")

            self._is_validated = True
            publish_lock = asyncio.Lock()
            self._lidar_pub_task = asyncio.create_task(
                self._publish_task_loop(channel, publish_lock)
            )
            self._data_pub_task = asyncio.create_task(
                self._publish_subscription_data(channel, publish_lock)
            )

        else:
            logger.info("validation failed (encrypted key mismatch)")

    async def _on_video_message(
        self, _channel: RTCDataChannel, message: VideoMessage
    ) -> None:
        match message.data:
            case "on":
                self._video_track.enabled = True
                logger.info("video enabled")
            case "off":
                self._video_track.enabled = False
                logger.info("video disabled")

    async def _on_subscribe_message(
        self, _channel: RTCDataChannel, message: SubscribeMessage
    ) -> None:
        if message.topic in go2m.TOPIC_TO_MESSAGE_TYPE:
            self._subscriptions.add(message.topic)
            logger.info(f"subscribed: {message.topic}")
        elif message.topic == "rt/utlidar/voxel_map_compressed":
            self._enable_lidar_publishing = True
            logger.info("lidar publishing enabled")
        else:
            logger.info(f"unknown topic: {message.topic}")

    async def _on_unsubscribe_message(
        self, _channel: RTCDataChannel, message: UnsubscribeMessage
    ) -> None:
        if message.topic in self._subscriptions:
            self._subscriptions.remove(message.topic)
            logger.info(f"unsubscribed: {message.topic}")
        elif message.topic == "rt/utlidar/voxel_map_compressed":
            self._enable_lidar_publishing = False
            logger.info("lidar publishing disabled")
        else:
            logger.info(f"unknown topic: {message.topic}")

    async def _on_datachannel_message(
        self, channel: RTCDataChannel, message: str | bytes
    ) -> None:
        logger.info(f"Received datachannel message: {type(message).__name__}")
        try:
            if isinstance(message, bytes):
                logger.debug(f"Binary message, length={len(message)}")
                return

            logger.info(f"Message content: {message[:200]}...")
            payload = GenericMessage.model_validate_json(message)
            logger.info(f"Parsed message type={payload.type}, topic={payload.topic}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"non-JSON datachannel message: {e}")
            return

        if payload.type == "validation":
            await self._on_validation_message(
                channel, ValidationMessage.model_validate_json(message)
            )

        elif payload.type == "vid":
            await self._on_video_message(
                channel, VideoMessage.model_validate_json(message)
            )

        elif payload.type == "subscribe":
            await self._on_subscribe_message(
                channel, SubscribeMessage.model_validate_json(message)
            )

        elif payload.type == "unsubscribe":
            await self._on_unsubscribe_message(
                channel, UnsubscribeMessage.model_validate_json(message)
            )

    async def _publish_subscription_data(
        self, channel: RTCDataChannel, publish_lock: asyncio.Lock
    ) -> None:
        logger.info("starting subscription data publish task loop")
        try:
            while True:
                await asyncio.sleep(self._publish_period)

                subscriptions = list(self._subscriptions)
                if not subscriptions:
                    # No subscriptions yet, just wait
                    continue

                logger.info(f"Publishing to {len(subscriptions)} subscriptions")

                for topic in subscriptions:
                    maker = go2m.TOPIC_TO_MESSAGE_TYPE.get(topic, None)
                    if maker is None:
                        logger.warning(f"No maker for topic {topic}")
                        continue

                    try:
                        msg = MessageMessage(
                            topic=topic, data=maker().model_dump()
                        ).model_dump_json()
                        logger.info(f"Sending message to topic {topic}: {msg[:100]}...")
                        async with publish_lock:
                            channel.send(msg)
                        logger.info(f"Message sent to topic {topic}")
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"send failed for {topic}: {e}")
        except Exception as ex:
            logger.exception("topic publish loop failed", exc_info=ex)
            raise
        finally:
            logger.info("subscription data publish task loop stopped")

    async def _publish_task_loop(
        self, channel: RTCDataChannel, publish_lock: asyncio.Lock
    ) -> None:
        logger.info("starting lidar publish task loop")
        try:
            while True:
                async with self._lidar_frame_cv:
                    await self._lidar_frame_cv.wait_for(
                        lambda: self._lidar_frame is not None
                    )
                    if self._lidar_frame is None:
                        continue

                    lidar_frame = self._lidar_frame
                    self._lidar_frame = None

                if self._pc.connectionState != "connected":
                    logger.warning("PC not connected")
                    continue
                if not self._is_validated:
                    logger.warning("PC not validated")
                    continue

                if self._enable_lidar_publishing:
                    try:
                        async with publish_lock:
                            channel.send(lidar_frame)
                        logger.debug("Sent lidar frame")
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"failed to send lidar frame, {e=}")
        except Exception as ex:
            logger.exception("lidar publish loop failed", exc_info=ex)
            raise
        finally:
            logger.info("lidar publish task loop stopped")
