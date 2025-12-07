# This is a mock go2 webrtc server that reads from a webcam and sends the frames to the client.

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
import cv2
import numpy as np
from av import VideoFrame  # pyright: ignore[reportPrivateImportUsage]
from fractions import Fraction
import threading

from go2_robot_sdk.domain.constants.webrtc_topics import RTC_TOPIC

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class WebcamVideoTrack(MediaStreamTrack):
    """Video track that reads from webcam and displays it locally."""
    kind = "video"

    def __init__(self, camera_index: int = 0, width: int = 640, height: int = 480, fps: int = 30):
        super().__init__()
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.fps = fps
        self.enabled = False
        
        # Open webcam
        logger.info(f"Attempting to open camera {camera_index}...")
        self.cap = cv2.VideoCapture(camera_index)
        if not self.cap.isOpened():
            raise RuntimeError(f"Failed to open camera {camera_index}. Make sure the camera is connected and not being used by another application.")
        
        # Set camera properties
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        
        # Test reading a frame to verify webcam works
        ret, test_frame = self.cap.read()
        if not ret or test_frame is None:
            self.cap.release()
            raise RuntimeError(f"Camera {camera_index} opened but failed to read frames. Check camera permissions and availability.")
        
        actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
        logger.info(f"Camera {camera_index} opened successfully. Actual resolution: {actual_width}x{actual_height}, FPS: {actual_fps}")
        logger.info(f"Test frame read: {test_frame.shape}")
        
        # Frame timing
        self._frame_index = 0
        self._time_base = Fraction(1, fps)
        self._frame_interval = 1.0 / fps
        
        # Display window
        self._display_enabled = True
        self._display_thread = None
        self._latest_frame = None
        self._frame_lock = threading.Lock()
        
        # Background frame reading task
        self._frame_reading_task = None
        self._frame_reading_running = False
        
        logger.info(f"WebcamVideoTrack initialized: {width}x{height} @ {fps}fps")
        
        # Start background frame reading
        self._start_frame_reading()

    def _display_loop(self):
        """Display loop running in separate thread."""
        window_name = "Webcam Feed (press 'q' to quit display)"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        logger.info(f"Display window '{window_name}' created")
        
        frame_count = 0
        try:
            while self._display_enabled:
                with self._frame_lock:
                    frame = self._latest_frame
                
                if frame is not None:
                    cv2.imshow(window_name, frame)
                    frame_count += 1
                    if frame_count % 30 == 0:  # Log every 30 frames (~1 second at 30fps)
                        logger.debug(f"Displayed {frame_count} frames, latest frame shape: {frame.shape}")
                else:
                    if frame_count == 0:
                        logger.warning("No frames available for display yet")
                
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    logger.info("Display window closed by user")
                    self._display_enabled = False
                    break
                
                # Small sleep to avoid busy waiting
                threading.Event().wait(0.033)  # ~30fps display rate
        except Exception as e:
            logger.error(f"Display loop error: {e}", exc_info=True)
        finally:
            cv2.destroyAllWindows()
            logger.info("Display window closed")

    async def recv(self) -> VideoFrame:
        """Read frame from webcam and return as VideoFrame."""
        # Start background frame reading if not already started
        if self._frame_reading_task is None or (hasattr(self._frame_reading_task, 'done') and self._frame_reading_task.done()):
            if self._frame_reading_running:
                try:
                    loop = asyncio.get_running_loop()
                    self._frame_reading_task = loop.create_task(self._frame_reading_loop())
                    logger.info("Started background frame reading task from recv()")
                except RuntimeError:
                    pass
        
        await asyncio.sleep(self._frame_interval)
        
        if not self.enabled:
            # Return black frame when disabled
            img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        else:
            # Get the latest frame from background reading (or read directly if not available)
            with self._frame_lock:
                frame = self._latest_frame
            
            if frame is not None:
                # Convert BGR to RGB for VideoFrame
                img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
                # Log periodically to verify frames are being sent
                if self._frame_index % (self.fps * 5) == 0:  # Every 5 seconds
                    logger.info(f"Webcam frame #{self._frame_index} sent over WebRTC: {frame.shape}")
            else:
                # Fallback: try to read directly if background reading hasn't started yet
                if self._frame_index == 0:
                    logger.warning("No frame available from background reading, reading directly...")
                if self.cap.isOpened():
                    ret, frame = self.cap.read()
                    if ret and frame is not None:
                        if frame.shape[1] != self.width or frame.shape[0] != self.height:
                            frame = cv2.resize(frame, (self.width, self.height))
                        img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        # Update latest frame for display
                        with self._frame_lock:
                            self._latest_frame = frame
                    else:
                        img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
                else:
                    img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        
        # Create VideoFrame
        video_frame = VideoFrame.from_ndarray(img, format="rgb24")
        video_frame.pts = self._frame_index
        video_frame.time_base = self._time_base
        self._frame_index += 1
        
        return video_frame

    def _start_frame_reading(self):
        """Start background task to continuously read frames from webcam."""
        if self._frame_reading_task is None or (hasattr(self._frame_reading_task, 'done') and self._frame_reading_task.done()):
            self._frame_reading_running = True
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    self._frame_reading_task = loop.create_task(self._frame_reading_loop())
                    logger.info("Started background frame reading task")
                else:
                    # If no event loop is running, we'll start it when recv() is first called
                    logger.info("Event loop not running yet, will start frame reading when recv() is called")
            except RuntimeError:
                # No event loop in this thread, will start when recv() is called
                logger.info("No event loop available, will start frame reading when recv() is called")

    async def _frame_reading_loop(self):
        """Continuously read frames from webcam in background."""
        logger.info("Frame reading loop started")
        while self._frame_reading_running:
            try:
                if not self.cap.isOpened():
                    logger.error("Webcam is not opened in frame reading loop")
                    await asyncio.sleep(1.0)
                    continue
                
                ret, frame = self.cap.read()
                if ret and frame is not None:
                    # Resize if needed
                    if frame.shape[1] != self.width or frame.shape[0] != self.height:
                        frame = cv2.resize(frame, (self.width, self.height))
                    
                    # Update latest frame for display (BGR format for OpenCV)
                    with self._frame_lock:
                        self._latest_frame = frame
                else:
                    logger.warning("Failed to read frame in background loop")
                    # Try to reopen
                    self.cap.release()
                    await asyncio.sleep(0.1)
                    self.cap = cv2.VideoCapture(self.camera_index)
                    if self.cap.isOpened():
                        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                        self.cap.set(cv2.CAP_PROP_FPS, self.fps)
                
                await asyncio.sleep(self._frame_interval)
            except Exception as e:
                logger.error(f"Error in frame reading loop: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    def start_display(self):
        """Start the display thread."""
        if self._display_thread is None or not self._display_thread.is_alive():
            self._display_enabled = True
            self._display_thread = threading.Thread(target=self._display_loop, daemon=True)
            self._display_thread.start()
            logger.info("Started webcam display window")

    def stop_display(self):
        """Stop the display thread."""
        self._display_enabled = False
        if self._display_thread and self._display_thread.is_alive():
            self._display_thread.join(timeout=1.0)
        cv2.destroyAllWindows()

    def stop(self):
        """Cleanup resources."""
        # Stop frame reading
        self._frame_reading_running = False
        if self._frame_reading_task and not self._frame_reading_task.done():
            self._frame_reading_task.cancel()
        
        self.stop_display()
        if self.cap.isOpened():
            self.cap.release()
        logger.info("WebcamVideoTrack stopped")


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
    enc_bytes = base64.b64decode(enc_b64)
    cipher = PKCS1_v1_5.new(rsa_private)
    sentinel = b"__bad__"
    dec = cipher.decrypt(enc_bytes, sentinel)
    if dec == sentinel:
        raise ValueError("RSA decrypt failed")
    return dec.decode("utf-8")


# ------------------------------
# Fake data generators
# ------------------------------
def make_lowstate() -> dict[str, t.Any]:
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
    RTC_TOPIC["ULIDAR_ARRAY"]: lambda: None,
}


# ------------------------------
# Mock server with webcam
# ------------------------------
class MockGo2EncryptedServerWithWebcam:
    """
    A mock "robot" server that:
      - Reads video from webcam
      - Displays webcam feed locally in OpenCV window
      - Sends webcam video over WebRTC
      - Accepts encrypted WebRTC offers
      - Handles validation and subscriptions
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9991, publish_hz: float = 0.2, 
                 camera_index: int = 0, video_width: int = 640, video_height: int = 480, video_fps: int = 30):
        self.host = host
        self.port = port
        self.publish_interval = 1.0 / publish_hz

        # RSA keypair for the session
        self._rsa_key = RSA.generate(2048)
        self._rsa_pub_pem_bytes = self._rsa_key.publickey().export_key(format="PEM")
        self._rsa_pub_pem_b64 = base64.b64encode(self._rsa_pub_pem_bytes).decode("utf-8")

        # Path ending for validation
        self._prefix10 = "JJJJJJJJJJ"
        self._suffix10 = "AABBCCDDEE"
        self._path_ending = "01234"

        # Create webcam video track
        try:
            self._video_track = WebcamVideoTrack(
                camera_index=camera_index,
                width=video_width,
                height=video_height,
                fps=video_fps
            )
            # Enable video by default
            self._video_track.enabled = True
            # Start display window
            self._video_track.start_display()
            logger.info(f"Webcam video track created and enabled. Camera {camera_index} is ready.")
        except Exception as e:
            logger.error(f"Failed to initialize webcam: {e}")
            raise

        self._app = web.Application()
        self._app.add_routes([
            web.post("/con_notify", self.on_con_notify),
            web.post(r"/con_ing_{ending}", self.on_con_ing),
        ])

        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

        self._pcs: set[RTCPeerConnection] = set()
        self._validated: dict[RTCPeerConnection, bool] = {}
        self._subscriptions: dict[RTCPeerConnection, set[str]] = {}
        self._pub_tasks: dict[RTCPeerConnection, asyncio.Task[None]] = {}
        self._blackholes: dict[RTCPeerConnection, MediaBlackhole] = {}
        self._pending_validation: dict[RTCPeerConnection, str | None] = {}

    # --------- HTTP Handlers ----------
    async def on_con_notify(self, request: web.Request) -> web.Response:
        """Return base64-encoded JSON with data1."""
        data1 = f"{self._prefix10}{self._rsa_pub_pem_b64}{self._suffix10}"
        payload = {
            "code": 0,
            "msg": "ok",
            "data1": data1,
        }
        text = json.dumps(payload, separators=(",", ":"))
        encoded = base64.b64encode(text.encode("utf-8")).decode("utf-8")
        return web.Response(text=encoded, content_type="text/plain")

    async def on_con_ing(self, request: web.Request) -> web.Response:
        """Decrypt, complete WebRTC, and return AES-encrypted answer."""
        ending = request.match_info.get("ending")
        if ending != self._path_ending:
            logger.warning(f"Bad path ending {ending}, expected {self._path_ending}")
            return web.Response(status=404, text="not found")

        raw_body = await request.text()
        try:
            body = json.loads(raw_body)
        except Exception:
            return web.Response(status=400, text="bad json")

        enc_data1 = body.get("data1")
        enc_data2 = body.get("data2")
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

            validation_key = uuid.uuid4().hex
            self._pending_validation[pc] = validation_key

            try:
                channel.send(json.dumps({"type": "validation", "data": validation_key}))
                logger.info("Sent validation key to client")
            except Exception as e:
                logger.warning(f"Failed to send validation key: {e}")

            @channel.on("message")  # pyright: ignore[reportUntypedFunctionDecorator]
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
                    data_str = data if isinstance(data, str) else ""
                    pending = self._pending_validation.get(pc)
                    if pending:
                        expected = _ValidationCryptoServer.encrypt_key(pending)
                        if data_str == expected:
                            self._validated[pc] = True
                            logger.info("validation accepted (encrypted key matched)")
                            try:
                                channel.send(json.dumps({"type": "validation", "data": "Validation Ok."}))
                            except Exception as e:
                                logger.warning(f"Failed to send validation ack: {e}")
                            self._start_publisher(pc, channel)
                        else:
                            logger.info("validation failed (encrypted key mismatch)")
                    else:
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
                        logger.info("Video enabled via client command")
                    elif isinstance(data, str) and data.lower() == "off":
                        self._video_track.enabled = False
                        logger.info("Video disabled via client command")

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

        # Provide webcam video track
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
                while True:
                    await asyncio.sleep(self.publish_interval)
                    if pc.connectionState != "connected":
                        continue
                    if not self._validated.get(pc):
                        continue
                    for topic in list(self._subscriptions.get(pc, set())):
                        maker = TOPICS.get(topic)
                        if not maker:
                            continue
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
        logger.info(f"MockGo2EncryptedServerWithWebcam listening on http://{self.host}:{self.port}")

    async def stop(self):
        for pc in list(self._pcs):
            await self._cleanup_pc(pc)
        if self._video_track:
            self._video_track.stop()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
        logger.info("MockGo2EncryptedServerWithWebcam stopped")


# ------------------------------
# Entry point
# ------------------------------
async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Mock GO2 WebRTC server with webcam")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=9991, help="Server port")
    parser.add_argument("--camera", type=int, default=0, help="Camera index (default: 0)")
    parser.add_argument("--width", type=int, default=640, help="Video width (default: 640)")
    parser.add_argument("--height", type=int, default=480, help="Video height (default: 480)")
    parser.add_argument("--fps", type=int, default=30, help="Video FPS (default: 30)")
    parser.add_argument("--publish-hz", type=float, default=0.2, help="Publish frequency in Hz (default: 0.2)")
    args = parser.parse_args()

    srv = MockGo2EncryptedServerWithWebcam(
        host=args.host,
        port=args.port,
        publish_hz=args.publish_hz,
        camera_index=args.camera,
        video_width=args.width,
        video_height=args.height,
        video_fps=args.fps
    )
    
    try:
        await srv.start()
        logger.info("Server started. Press Ctrl+C to stop.")
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await srv.stop()


if __name__ == "__main__":
    asyncio.run(main())

