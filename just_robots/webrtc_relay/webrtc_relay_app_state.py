import dataclasses
import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional
from fastapi.requests import Request
from aiortc import RTCPeerConnection, RTCDataChannel, MediaStreamTrack
from aiortc.contrib.media import MediaRelay

from go2_robot_sdk.infrastructure.webrtc.go2_connection import Go2Connection

if TYPE_CHECKING:
    from go2_robot_sdk.webrtc_relay.webrtc_stats_monitor import WebRTCStatsMonitor

logger = logging.getLogger(__name__)

@dataclasses.dataclass
class WebRTCRelayAppState:
    media_relay: MediaRelay = dataclasses.field(default_factory=MediaRelay)
    go2: Go2Connection | None = None
    relay_rtc_peer_connection: RTCPeerConnection | None = None
    relay_rtc_data_channel: RTCDataChannel | None = None
    go2_video_track: MediaStreamTrack | None = None
    subscribed_topics: set[str] = dataclasses.field(default_factory=set)
    # WebRTC stats monitoring
    relay_stats_monitor: 'WebRTCStatsMonitor | None' = None  # RELAY→CLIENT
    client_to_relay_stats_monitor: 'WebRTCStatsMonitor | None' = None  # CLIENT→RELAY (from relay's perspective)
    go2_stats_monitor: 'WebRTCStatsMonitor | None' = None  # GO2→RELAY (from relay's perspective)
    # Connection tracking for single-user access control and idle timeout
    current_user_id: Optional[str] = None  # Firebase UID of connected user
    current_user_email: Optional[str] = None  # Email of connected user
    last_activity_time: Optional[float] = None  # Timestamp of last activity
    idle_timeout_task: Optional[asyncio.Task] = None  # Background task for idle timeout monitoring

    async def close_rtc_relay_connection(self):
        # Stop stats monitoring
        if self.relay_stats_monitor is not None:
            await self.relay_stats_monitor.stop()
            self.relay_stats_monitor = None
        if self.client_to_relay_stats_monitor is not None:
            await self.client_to_relay_stats_monitor.stop()
            self.client_to_relay_stats_monitor = None
        
        if self.relay_rtc_peer_connection is not None:
            logger.info(f"closing existing rtc peer connection. {self.relay_rtc_peer_connection}")
            try:
                await self.relay_rtc_peer_connection.close()
            except Exception as exception:
                logger.warning(f"failed to close existing relay rtc peer connection. {exception=}")
            finally:
                self.relay_rtc_peer_connection = None

        if self.relay_rtc_data_channel is not None:
            logger.info(f"closing existing rtc peer data channel. {self.relay_rtc_data_channel}")
            try:
                await asyncio.to_thread(self.relay_rtc_data_channel.close)
            except Exception as exception:
                logger.warning(f"failed to close existing relay rtc peer data connection. {exception=}")
            finally:
                self.relay_rtc_data_channel = None 
    
    def update_activity(self):
        """Update last activity timestamp. Call this on every user interaction."""
        self.last_activity_time = time.time()
    
    async def start_idle_timeout_monitor(self, timeout_seconds: float = 300.0):
        """Start monitoring for idle timeout. Auto-disconnect after timeout."""
        # Stop any existing idle timeout task
        if self.idle_timeout_task and not self.idle_timeout_task.done():
            self.idle_timeout_task.cancel()
            try:
                await self.idle_timeout_task
            except asyncio.CancelledError:
                pass
        
        async def monitor_idle():
            check_interval = 10.0  # Check every 10 seconds
            while self.current_user_id is not None:
                await asyncio.sleep(check_interval)
                if self.last_activity_time is None:
                    continue
                
                idle_time = time.time() - self.last_activity_time
                if idle_time >= timeout_seconds:
                    logger.warning(
                        f"User {self.current_user_id} ({self.current_user_email}) has been idle for "
                        f"{idle_time:.1f}s (timeout: {timeout_seconds}s). Auto-disconnecting..."
                    )
                    await self._force_disconnect_idle_user()
                    break
        
        self.idle_timeout_task = asyncio.create_task(monitor_idle())
        logger.info(f"Started idle timeout monitor (timeout: {timeout_seconds}s)")
    
    async def _force_disconnect_idle_user(self):
        """Force disconnect due to idle timeout. Called by idle timeout monitor."""
        logger.info("Forcing disconnect due to idle timeout")
        
        # Stop idle monitoring
        if self.idle_timeout_task:
            self.idle_timeout_task.cancel()
            try:
                await self.idle_timeout_task
            except asyncio.CancelledError:
                pass
            self.idle_timeout_task = None
        
        # Cleanup connections
        await self.close_rtc_relay_connection()
        if self.go2:
            try:
                await self.go2.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting GO2 during idle timeout: {e}")
            self.go2 = None
            self.go2_video_track = None
        
        # Clear user tracking
        disconnected_user = self.current_user_id
        disconnected_email = self.current_user_email
        self.current_user_id = None
        self.current_user_email = None
        self.last_activity_time = None
        
        logger.info(f"Successfully disconnected idle user: {disconnected_user} ({disconnected_email})")

def get_app_state(request: Request) -> WebRTCRelayAppState:
    return request.app.state.state

