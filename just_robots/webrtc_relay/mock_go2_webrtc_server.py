from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack  # type: ignore
from aiortc.contrib.media import MediaBlackhole
import asyncio
import base64
from Crypto.PublicKey import RSA
from Crypto.Cipher import AES, PKCS1_v1_5
import hashlib
import json
import logging
import typing as t
import uuid
import base64
import json

from go2_robot_sdk.webrtc_relay.mock_go2_video_track import MockGo2VideoTrack
from go2_robot_sdk.domain.constants.webrtc_topics import RTC_TOPIC

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class _ValidationCryptoServer:
    @staticmethod
    def _hex_to_base64(hex_str: str) -> str:
        raw = bytes.fromhex(hex_str)
        return base64.b64encode(raw).decode("utf-8")

    @staticmethod
    def _md5_hex(s: str) -> str:
        h = hashlib.md5()
        h.update(s.encode("utf-8"))
        return h.hexdigest()

    @staticmethod
    def encrypt_key(key: str) -> str:
        # Mirrors client's ValidationCrypto.encrypt_key
        prefixed = f"UnitreeGo2_{key}"
        md5_hex = _ValidationCryptoServer._md5_hex(prefixed)
        return _ValidationCryptoServer._hex_to_base64(md5_hex)

def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len]) * pad_len

def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise ValueError("empty data")
    pad_len = data[-1]
    if pad_len == 0 or pad_len > len(data):
        raise ValueError("bad pad")
    return data[:-pad_len]

def aes_ecb_encrypt_base64_str(plain_text: str, key_str: str) -> str:
    # key_str is 32 ASCII chars (hex)
    key = key_str.encode("utf-8")
    cipher = AES.new(key, AES.MODE_ECB)
    padded = _pkcs7_pad(plain_text.encode("utf-8"), 16)
    enc = cipher.encrypt(padded)
    return base64.b64encode(enc).decode("utf-8")

def aes_ecb_decrypt_base64_str(enc_b64: str, key_str: str) -> str:
    key = key_str.encode("utf-8")
    cipher = AES.new(key, AES.MODE_ECB)
    enc = base64.b64decode(enc_b64)
    dec_padded = cipher.decrypt(enc)
    dec = _pkcs7_unpad(dec_padded)
    return dec.decode("utf-8")

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

# ------------------------------
# Fake data generators (schema-correct, junk content)
# ------------------------------
def make_lowstate() -> dict[str, t.Any]:
    # 12 motors (example); q/qd/qdd/tau floats
    motors = []
    for _ in range(12):
        motors.append({
            "q": 0.0,
            "qd": 0.0,
            "qdd": 0.0,
            "tau": 0.0,
        })
    return {"motor_state": motors}

def make_sportmodestate() -> dict[str, t.Any]:
    return {
        "mode": "mock",
        "progress": 0,
        "gait_type": "mock",
        "position": [0.0, 0.0, 0.0],
        "body_height": 0.0,
        "velocity": 0.0,
        "range_obstacle": [],
        "foot_force": 0.0,
        "foot_position_body": [0.0, 0.0, 0.0, 0.0],
        "foot_speed_body": [0.0, 0.0, 0.0, 0.0],
        "imu_state": {
            "quaternion": [0.0, 0.0, 0.0, 1.0],
            "accelerometer": [0.0, 0.0, 0.0],
            "gyroscope": [0.0, 0.0, 0.0],
            "rpy": [0.0, 0.0, 0.0],
            "temperature": 0.0,
        },
    }

def make_robot_pose() -> dict[str, t.Any]:
    return {
        "pose": {
            "position": {
                "x": 0.0,
                "y": 0.0,
                "z": 0.0,
            },
            "orientation": {
                "x": 0.0,
                "y": 0.0,
                "z": 0.0,
                "w": 1.0,
            },
        }
    }


TOPICS = {
    RTC_TOPIC["LOW_STATE"]: make_lowstate,
    RTC_TOPIC["LF_SPORT_MOD_STATE"]: make_sportmodestate,
    RTC_TOPIC["ROBOTODOM"]: make_robot_pose,
    RTC_TOPIC["ULIDAR_ARRAY"]: lambda : None,
}


# ------------------------------
# Mock server
# ------------------------------
class MockGo2EncryptedServer:
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

    def __init__(self, host: str = "127.0.0.1", port: int = 9991, publish_hz: float = 0.2):
        self.host = host
        self.port = port
        self.publish_interval = 1.0 / publish_hz

        self.lidar_frames = self._read_lidar_dump('lidar_dump.txt')

        # RSA keypair for the session
        self._rsa_key = RSA.generate(2048)
        self._rsa_pub_pem_bytes = self._rsa_key.publickey().export_key(format="PEM")
        # Client expects base64-wrapped PEM string
        self._rsa_pub_pem_b64 = base64.b64encode(self._rsa_pub_pem_bytes).decode("utf-8")

        # Compose data1: 10-char prefix + base64(PEM) + 10-char suffix
        # Suffix crafted so PathCalculator.calc_local_path_ending -> "01234"
        #   last 10 chars are split into pairs; we take 2nd char of each and map A..J -> 0..9
        # Using: "AABBCCDDEE" -> indices of 'A','B','C','D','E' => "01234"
        self._prefix10 = "JJJJJJJJJJ"    # arbitrary 10 chars
        self._suffix10 = "AABBCCDDEE"    # yields "01234"
        self._path_ending = "01234"

        self._app = web.Application()
        self._app.add_routes([
            web.post("/con_notify", self.on_con_notify),
            web.post(r"/con_ing_{ending}", self.on_con_ing),
        ])
        from pprint import pformat
        logging.info(pformat([rr.get_info() for rr in self._app.router.routes()]))
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

        self._pcs: set[RTCPeerConnection] = set()
        self._validated: dict[RTCPeerConnection, bool] = {}
        self._subscriptions: dict[RTCPeerConnection, set[str]] = {}
        self._pub_tasks: dict[RTCPeerConnection, asyncio.Task[None]] = {}
        self._blackholes: dict[RTCPeerConnection, MediaBlackhole] = {}
        self._pending_validation: dict[RTCPeerConnection, str | None] = {}

        self._video_track = MockGo2VideoTrack()

    def _read_lidar_dump(self, path: str) -> t.Dict[int, t.Dict[str, t.Any]]:
        """
        Read a JSONL lidar dump where each line is {"frame": "<base64>"}.
        Returns a dict mapping line index -> {"b64": str, "raw": bytes}.
        """
        frames: t.Dict[int, bytes] = {}
        with open(path, "r", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    # skip invalid lines
                    continue
                b64 = obj.get("frame")
                if not b64:
                    continue
                try:
                    raw = base64.b64decode(b64)
                except Exception:
                    raw = b""  # keep empty on decode failure
                frames[i] = raw
        return frames

    # --------- HTTP Handlers ----------
    async def on_con_notify(self, request: web.Request) -> web.Response:
        """
        Return base64-encoded JSON with data1:
          data1 = <10-prefix> + base64(PEM) + <10-suffix>
        """
        data1 = f"{self._prefix10}{self._rsa_pub_pem_b64}{self._suffix10}"
        payload = {
            "code": 0,
            "msg": "ok",
            "data1": data1,
        }
        text = json.dumps(payload, separators=(",", ":"))
        encoded = base64.b64encode(text.encode("utf-8")).decode("utf-8")
        # Client does: base64.b64decode(response.text).decode('utf-8') -> json -> data1
        return web.Response(text=encoded, content_type="text/plain")

    async def on_con_ing(self, request: web.Request) -> web.Response:
        """
        Decrypt, complete WebRTC, and return AES-encrypted answer as plain text.
        """
        ending = request.match_info.get("ending")
        if ending != self._path_ending:
            logger.warning(f"Bad path ending {ending}, expected {self._path_ending}")
            return web.Response(status=404, text="not found")

        # Body was posted as JSON string, but may have Content-Type: application/x-www-form-urlencoded
        raw_body = await request.text()
        try:
            body = json.loads(raw_body)
        except Exception:
            return web.Response(status=400, text="bad json")

        enc_data1 = body.get("data1")  # AES(offer_json)
        enc_data2 = body.get("data2")  # RSA(AES_key)
        if not enc_data1 or not enc_data2:
            return web.Response(status=400, text="missing fields")

        # 1) RSA-decrypt AES key
        try:
            aes_key = rsa_decrypt_aes_key_b64(enc_data2, self._rsa_key)
        except Exception as e:
            logger.exception("RSA decrypt failed")
            return web.Response(status=400, text=f"rsa decrypt failed: {e}")

        # 2) AES-decrypt offer json
        try:
            offer_json_str = aes_ecb_decrypt_base64_str(enc_data1, aes_key)
            offer_obj = json.loads(offer_json_str)
            # Expect keys: id, sdp, type, token (we ignore id/token)
            remote_sdp = offer_obj["sdp"]
            remote_type = offer_obj["type"]
        except Exception as e:
            logger.exception("AES decrypt of offer failed")
            return web.Response(status=400, text=f"aes decrypt failed: {e}")

        # 3) Create PC and finish SDP
        pc = RTCPeerConnection()
        self._pcs.add(pc)
        self._validated[pc] = False
        self._pending_validation[pc] = None
        self._subscriptions[pc] = set()
        self._blackholes[pc] = MediaBlackhole()

        logger.info("Created RTCPeerConnection (encrypted flow)")

        @pc.on("datachannel")
        def on_datachannel(channel):  # pyright: ignore[reportUnusedFunction] 
            logger.info(f"datachannel: {channel.label}")

            validation_key = uuid.uuid4().hex  # 32 hex chars
            self._pending_validation[pc] = validation_key

            # Send the plaintext key to the client
            try:
                channel.send(json.dumps({"type": "validation", "data": validation_key}))
                logger.info("Sent validation key to client")
            except Exception as e:
                logger.warning(f"Failed to send validation key: {e}")

            @channel.on("message")  # pyright: ignore[reportUntypedFunctionDecorator]s
            def on_message(message):  # pyright: ignore[reportUnusedFunction]
                try:
                    if isinstance(message, bytes):
                        return
                    payload = json.loads(message)
                except Exception:
                    logger.warning("non-JSON datachannel message")
                    return

                mtype = payload.get("type")
                topic = payload.get("topic", "")
                data = payload.get("data")

                if mtype == "validation":
                    # Client is responding with encrypted key (or asking to start)
                    data_str = data if isinstance(data, str) else ""
                    pending = self._pending_validation.get(pc)
                    if pending:
                        expected = _ValidationCryptoServer.encrypt_key(pending)
                        if data_str == expected:
                            # success
                            self._validated[pc] = True
                            logger.info("validation accepted (encrypted key matched)")
                            # Acknowledge exactly as specified
                            try:
                                channel.send(json.dumps({"type": "validation", "data": "Validation Ok."}))
                            except Exception as e:
                                logger.warning(f"Failed to send validation ack: {e}")
                            # Now start publishing loop
                            self._start_publisher(pc, channel)
                        else:
                            logger.info("validation failed (encrypted key mismatch)")
                            # You can optionally notify the client, or stay silent.
                            # Example (comment out if you prefer silence):
                            # channel.send(json.dumps({"type": "validation", "data": "Invalid Key."}))
                    else:
                        # We haven't sent a key yet (or it was cleared) — resend a new one
                        validation_key = uuid.uuid4().hex
                        self._pending_validation[pc] = validation_key
                        try:
                            channel.send(json.dumps({"type": "validation", "data": validation_key}))
                            logger.info("Re-sent validation key to client")
                        except Exception as e:
                            logger.warning(f"Failed to send validation key: {e}")

                elif mtype == "vid":
                    if isinstance(data, str) and data.lower() == "on":
                        self._video_track.enabled = True
                        logger.info("video enabled")

                elif mtype == "subscribe":
                    if topic in TOPICS:
                        self._subscriptions[pc].add(topic)
                        logger.info(f"subscribed: {topic}")
                    else:
                        logger.info(f"unknown topic: {topic}")

        @pc.on("connectionstatechange")
        async def on_state_change():  # pyright: ignore[reportUnusedFunction]
            logger.info(f"PC state: {pc.connectionState}")
            if pc.connectionState in ("failed", "closed", "disconnected"):
                await self._cleanup_pc(pc)

        @pc.on("track")
        def on_track(track: MediaStreamTrack):  # pyright: ignore[reportUnusedFunction]
            logger.info(f"client sent track {track.kind}")
            self._blackholes[pc].addTrack(track)

        # Provide video track (black until enabled)
        pc.addTrack(self._video_track)

        # Finish SDP
        await pc.setRemoteDescription(RTCSessionDescription(sdp=remote_sdp, type=remote_type))
        answer = await pc.createAnswer()
        
        await pc.setLocalDescription(answer)  # type: ignore

        answer_json = json.dumps({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})
        # 4) AES-encrypt answer and return as plain text
        enc_answer = aes_ecb_encrypt_base64_str(answer_json, aes_key)
        return web.Response(text=enc_answer, content_type="text/plain")

    # ------------- Pub loop per-PC -------------
    def _start_publisher(self, pc: RTCPeerConnection, channel):
        if pc in self._pub_tasks and not self._pub_tasks[pc].done():
            return

        async def _pub():
            try:
                lidar_frame_num = 0
                lidar_frames_len = max(1, len(self.lidar_frames))
                while True:
                    await asyncio.sleep(self.publish_interval)
                    if pc.connectionState != "connected":
                        print("PC not connected")
                        continue
                    if not self._validated.get(pc):
                        print("PC not validated")
                        continue
                    for topic in list(self._subscriptions.get(pc, set())):
                        maker = TOPICS.get(topic)
                        if not maker:
                            print("Not a publisher")
                            continue
                        if topic == "rt/utlidar/voxel_map_compressed":
                            print("Publishing topic: " + topic)
                            try:
                                frame_bytes = self.lidar_frames[lidar_frame_num % lidar_frames_len]
                                channel.send(frame_bytes)
                            except Exception as e:
                                logger.warning(f"failed to send lidar frame #{lidar_frame_num}: {e}")
                            lidar_frame_num += 1
                            # # Do nothing for now, we don't know how to properly serialize lidar binary data
                            # pass
                        else:
                            msg = {"type": "msg", "topic": topic, "data": maker()}
                            try:
                                channel.send(json.dumps(msg))
                            except Exception as e:
                                logger.warning(f"send failed: {e}")
            except asyncio.CancelledError:
                pass
            except Exception as ex:
                logger.exception("publisher loop failed", exc_info=ex)

        self._pub_tasks[pc] = asyncio.create_task(_pub())

    async def _cleanup_pc(self, pc: RTCPeerConnection):
        if pc in self._pub_tasks:
            self._pub_tasks[pc].cancel()
            try:
                await self._pub_tasks[pc]
            except Exception:
                pass
            self._pub_tasks.pop(pc, None)

        if pc in self._blackholes:
            try:
                await self._blackholes[pc].stop()
            except Exception:
                pass
            self._blackholes.pop(pc, None)

        self._validated.pop(pc, None)
        self._pending_validation.pop(pc, None)
        self._subscriptions.pop(pc, None)

        if pc in self._pcs:
            await pc.close()
            self._pcs.remove(pc)
            logger.info("PC cleaned up")

    # --------- Lifecycle ----------
    async def start(self):
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        logger.info(f"MockGo2EncryptedServer listening on http://{self.host}:{self.port}")

    async def stop(self):
        for pc in list(self._pcs):
            await self._cleanup_pc(pc)
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
        logger.info("MockGo2EncryptedServer stopped")



# ------------------------------
# Entry point helper
# ------------------------------
async def main():
    srv = MockGo2EncryptedServer(host="127.0.0.1", port=9991, publish_hz=0.2)
    await srv.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await srv.stop()

if __name__ == "__main__":
    asyncio.run(main())
