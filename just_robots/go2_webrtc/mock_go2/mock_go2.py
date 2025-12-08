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

from just_robots.go2_webrtc.go2_connection_messages import (
    ConNotifyReply,
    WebRtcAnswer,
)
from just_robots.go2_webrtc.go2_lidar_dump_streamer import (
    MockGo2LidarDumpStreamer,
)
from just_robots.go2_webrtc.mock_go2.mock_go2_video_track import MockGo2VideoTrack
from just_robots.go2_webrtc.mock_go2.mock_go2_webrtc_peer_connection import (
    MockGo2WebRTCPeerConnection,
)
from just_robots.webrtc_relay.webrtc_relay_exceptions import (
    NotFoundException,
    StateException,
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

    async def stop(self) -> None:
        for pc in list(self._pcs):
            await pc.stop()

        self._lidar_task.cancel()
        self._video_track.stop()

    async def on_con_notify(self) -> str:
        """
        Return base64-encoded JSON with data1:
          data1 = <10-prefix> + base64(PEM) + <10-suffix>
        """
        # NOTE: this is the inverse of what the Go2Connection.connect function does
        data1 = f"{self._prefix10}{self._rsa_pub_pem_b64}{self._suffix10}"
        payload = ConNotifyReply(data1=data1)
        text = payload.model_dump_json(indent=0)
        encoded = base64.b64encode(text.encode("utf-8")).decode("utf-8")

        return encoded

    async def on_con_ing(self, path_ending: str, offer_json: str, aes_key: str) -> str:
        """
        Decrypt, complete WebRTC, and return AES-encrypted answer as plain text.
        """
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
        pc.addTrack(self._video_track)

        # Finish SDP
        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=remote_sdp, type=remote_type)
        )
        answer = await pc.createAnswer()
        if answer is None:
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
        if self._lidar_streamer:
            while True:
                lidar_frame = next(self._lidar_streamer)
                for pc in list(self._pcs):
                    await pc.queue_lidar_frame(lidar_frame)

                await asyncio.sleep(self.publish_interval)
