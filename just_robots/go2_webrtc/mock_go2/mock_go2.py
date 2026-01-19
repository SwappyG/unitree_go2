import asyncio
import base64
import json
import logging
from pathlib import Path

from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
)
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA
from go2_robot_sdk.infrastructure.webrtc.crypto.encryption import CryptoUtils

from just_robots.fastapi_utils.fastapi_exceptions import (
    NotFoundException,
    StateException,
)
from just_robots.go2_webrtc.go2_connection_messages import (
    ConNotifyReply,
    WebRtcAnswer,
)
from just_robots.go2_webrtc.mock_go2.mock_go2_lidar_dump_streamer import (
    MockGo2LidarDumpStreamer,
)
from just_robots.go2_webrtc.mock_go2.mock_go2_video_track import MockGo2VideoTrack
from just_robots.go2_webrtc.mock_go2.mock_go2_webrtc_peer_connection import (
    MockGo2WebRTCPeerConnection,
)

logger = logging.getLogger(__name__)


# NOTE (swapnil) - the go2_robot_sdk only provides the encoding function. This should be the
# inverse of that function
def rsa_decrypt_aes_key_b64(enc_b64: str, rsa_private: RSA.RsaKey) -> str:
    # Client RSA-encrypts the AES key as UTF-8 using PKCS1_v1_5; we decrypt it
    enc_bytes = base64.b64decode(enc_b64)
    cipher = PKCS1_v1_5.new(rsa_private)
    # PKCS1_v1_5 requires a sentinel
    sentinel = b"__bad__"
    dec = cipher.decrypt(enc_bytes, sentinel)
    if dec == sentinel:
        raise ValueError("RSA decrypt failed")
    return dec.decode("utf-8")


class MockGo2:
    """
    A lightweight fake "robot" that:
      - Accepts a POST /offer {sdp, type}
      - Creates an RTCPeerConnection with:
        * 1 DataChannel (on client creation)
        * 1 Video track (synthetic)
      - Handles datachannel messages:
        * {"type":"validation","topic":"","data":"..."} => enables publishing
        * {"type":"vid","topic":"","data":"on"} => enables video frames
        * {"type":"subscribe","topic":"<t>"} => subscribes to topic
      - Periodically sends:
        * {"type":"msg", "topic": "<t>", "data": {...}} as a JSON string
    """

    def __init__(
        self,
        publish_hz: float = 0.2,
        lidar_dump_filepath: Path | None = None,
    ):
        self.publish_interval = 1.0 / publish_hz

        self._lidar_streamer = None
        if lidar_dump_filepath:
            self._lidar_streamer = MockGo2LidarDumpStreamer(lidar_dump_filepath)

        # RSA keypair for the session
        self._rsa_key = RSA.generate(2048)
        self._rsa_pub_pem_bytes = self._rsa_key.publickey().export_key(format="PEM")
        # Client expects base64-wrapped PEM string
        self._rsa_pub_pem_b64 = base64.b64encode(self._rsa_pub_pem_bytes).decode(
            "utf-8"
        )

        # Compose data1: 10-char prefix + base64(PEM) + 10-char suffix
        # Suffix crafted so PathCalculator.calc_local_path_ending -> "01234"
        #   last 10 chars are split into pairs; we take 2nd char of each and map A..J -> 0..9
        # Using: "AABBCCDDEE" -> indices of 'A','B','C','D','E' => "01234"
        self._prefix10 = "JJJJJJJJJJ"  # arbitrary 10 chars
        self._suffix10 = "AABBCCDDEE"  # yields "01234"
        self._path_ending = "01234"

        self._pcs: set[MockGo2WebRTCPeerConnection] = set()

        self._video_track = MockGo2VideoTrack()

        self._lidar_task = asyncio.create_task(self._lidar_pub_task())
        self._cleanup_task = asyncio.create_task(self._cleanup_dead_connections_task())

    def _remove_dead_connections(self):
        """Remove dead connections from _pcs. Returns count of removed connections."""
        dead_pcs = {pc for pc in self._pcs if pc.is_dead}
        if dead_pcs:
            logger.info(f"Removing {len(dead_pcs)} dead connection(s)")
            self._pcs -= dead_pcs
            logger.info(f"Active connections: {len(self._pcs)}")

    async def _cleanup_dead_connections_task(self) -> None:
        """Periodically clean up dead connections."""
        try:
            while True:
                await asyncio.sleep(5.0)  # Check every 5 seconds
                self._remove_dead_connections()
        except asyncio.CancelledError:
            logger.info("Cleanup task cancelled")
        except Exception as ex:
            logger.exception("Cleanup task failed", exc_info=ex)

    async def stop(self) -> None:
        logger.info("Stopping MockGo2...")

        # Cancel background tasks
        self._cleanup_task.cancel()
        self._lidar_task.cancel()

        # Stop all peer connections
        await asyncio.gather(*[pc.stop() for pc in list(self._pcs)])

        self._pcs.clear()
        self._video_track.stop()
        logger.info("MockGo2 stopped")

    async def on_con_notify(self) -> str:
        """
        Return base64-encoded JSON with data1:
          data1 = <10-prefix> + base64(PEM) + <10-suffix>
        """
        # NOTE: this is the inverse of what the Go2Connection.connect function does
        logger.info(f"on_con_notify called (active connections: {len(self._pcs)})")
        data1 = f"{self._prefix10}{self._rsa_pub_pem_b64}{self._suffix10}"
        payload = ConNotifyReply(data1=data1)
        text = payload.model_dump_json(indent=0)
        encoded = base64.b64encode(text.encode("utf-8")).decode("utf-8")

        return encoded

    async def on_con_ing(self, path_ending: str, offer_json: str, aes_key: str) -> str:
        """
        Decrypt, complete WebRTC, and return AES-encrypted answer as plain text.
        """
        logger.info(f"on_con_ing called (active connections: {len(self._pcs)})")
        if path_ending != self._path_ending:
            logger.warning(
                f"Bad path ending {path_ending}, expected {self._path_ending}"
            )
            raise NotFoundException(f"bad path ending {path_ending=}")

        # Body was posted as JSON string, but may have Content-Type: application/x-www-form-urlencoded

        enc_data1 = offer_json  # AES(offer_json)
        enc_data2 = aes_key  # RSA(AES_key)

        # 1) RSA-decrypt AES key
        try:
            aes_key = rsa_decrypt_aes_key_b64(enc_data2, self._rsa_key)
        except Exception:
            logger.exception("RSA decrypt failed")
            raise

        # 2) AES-decrypt offer json
        try:
            offer_json_str = CryptoUtils.aes_decrypt(enc_data1, aes_key)
            offer_obj = json.loads(offer_json_str)
            # Expect keys: id, sdp, type, token (we ignore id/token)
            remote_sdp = offer_obj["sdp"]
            remote_type = offer_obj["type"]
        except Exception:
            logger.exception("AES decrypt of offer failed")
            raise

        # 3) Create PC and finish SDP
        pc = RTCPeerConnection()

        # Set remote description first (processes the client's offer)
        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=remote_sdp, type=remote_type)
        )

        # Debug: log the transceivers created from the offer
        transceivers = pc.getTransceivers()
        logger.info(f"Transceivers after setRemoteDescription: {len(transceivers)}")

        # Find that transceiver and update it to be sendonly + replace track
        video_transceiver_found = False
        for transceiver in transceivers:
            if transceiver.kind == "video":
                transceiver.direction = "sendonly"
                transceiver.sender.replaceTrack(self._video_track)
                logger.info(
                    f"Added video track to transceiver (direction now: {transceiver.direction})"
                )
                video_transceiver_found = True
                break

        if not video_transceiver_found:
            logger.warning("No video transceiver found - client did not request video")

        answer = await pc.createAnswer()
        if not answer:
            raise StateException(f"unable to generate answer from peer connection")

        await pc.setLocalDescription(answer)
        answer_json = WebRtcAnswer(
            sdp=pc.localDescription.sdp,
            type=pc.localDescription.type,
        )

        # 4) AES-encrypt answer and return as plain text
        enc_answer = CryptoUtils.aes_encrypt(answer_json.model_dump_json(), aes_key)
        logger.info("Created RTCPeerConnection (encrypted flow)")

        self._pcs.add(
            MockGo2WebRTCPeerConnection(
                peer_connection=pc, video_track=self._video_track
            )
        )
        return enc_answer

    async def _lidar_pub_task(self) -> None:
        """
        Publishes lidar frames to all active peer connections.
        Note: Dead connection cleanup is handled by _cleanup_dead_connections_task.
        """
        if not self._lidar_streamer:
            logger.info("No lidar streamer configured, lidar pub task exiting")
            return

        try:
            while True:
                lidar_frame = next(self._lidar_streamer)

                # Send to active connections (use list() snapshot for safe iteration)
                for pc in list(self._pcs):
                    if not pc.is_dead:
                        await pc.queue_lidar_frame(lidar_frame)

                await asyncio.sleep(self.publish_interval)
        except asyncio.CancelledError:
            logger.info("Lidar pub task cancelled")
        except Exception as ex:
            logger.exception("Lidar pub task failed", exc_info=ex)
