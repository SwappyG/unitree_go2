
from aiortc import MediaStreamTrack # type: ignore
import asyncio
from fastapi import HTTPException, Depends, APIRouter
import json
import logging
import time
from pydantic import BaseModel
import typing as t  # pyright: ignore[reportUnusedImport]
import os

from go2_robot_sdk.domain.constants.webrtc_topics import RTC_TOPIC
from go2_robot_sdk.infrastructure.webrtc.go2_connection import Go2Connection, RobotData
from go2_robot_sdk.webrtc_relay.webrtc_relay_app_state import WebRTCRelayAppState, get_app_state 
from go2_robot_sdk.webrtc_relay.webrtc_relay_exceptions import StateException
from go2_robot_sdk.webrtc_relay.webrtc_stats_monitor import WebRTCStatsMonitor
from go2_robot_sdk.webrtc_relay.webrtc_relay_client_video_viewer import display_video
from go2_robot_sdk.webrtc_relay.firebase_auth_server import verify_firebase_token


logger = logging.getLogger(__name__)
router = APIRouter()

TOPICS_TO_SUBSCRIBE_TO = [
    RTC_TOPIC['MULTIPLE_STATE'],
    RTC_TOPIC['SPORT_MOD_STATE'],
    RTC_TOPIC['LOW_STATE'],
    RTC_TOPIC['ULIDAR'], 
    RTC_TOPIC['ULIDAR_ARRAY'], 
    RTC_TOPIC['ULIDAR_STATE'],
    RTC_TOPIC['ROBOTODOM'],
]

def _on_go2_message(state: WebRTCRelayAppState, robot_data: RobotData):
    """
    Relay ONLY the parsed object (2nd arg) from GO2 -> PC.
    Serialize to JSON and send to the PC datachannel as TEXT.
    Sending data to client counts as activity (keeps connection alive).
    """
    # pc_dc: RTCDataChannel | None = state.relay_rtc_data_channel
    if not state.relay_rtc_data_channel or state.relay_rtc_data_channel.readyState != "open":
        logging.debug(f'got message from go2, but datachannel is not open {robot_data.raw_message=}')
        return

    # Update activity when sending data to client (keeps connection alive)
    state.update_activity()

    try:
        if isinstance(robot_data.raw_message, bytes):
            state.relay_rtc_data_channel.send(robot_data.raw_message)
        elif isinstance(robot_data.raw_message, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            # payload = json.dumps(robot_data.raw_message, separators=(",", ":"))
            state.relay_rtc_data_channel.send(robot_data.raw_message)
        else:
            print(f"unknown raw type {type(robot_data.raw_message)}")

    except Exception as exception:
        logger.warning(f"Failed to JSON-serialize GO2 message: {exception=}")

def _on_go2_validated(state: WebRTCRelayAppState, topics_to_subscribe_to: list[str]):
    logger.info("on validated called")
    try:
        if state.go2 is not None:
            asyncio.get_running_loop().create_task(state.go2.disableTrafficSaving(True))
            for topic in topics_to_subscribe_to:
                state.go2.data_channel.send(
                    json.dumps({"type": "subscribe", "topic": topic})
                )
            
            # Track subscribed topics in state
            state.subscribed_topics = set(topics_to_subscribe_to)

            state.go2.publish(RTC_TOPIC['ULIDAR_SWITCH'], 'on')
    except Exception as e:
        logger.error(f"Error in validated callback: {e}")


async def _on_go2_video_track(state: WebRTCRelayAppState, track: MediaStreamTrack, _robot_num: str|int):
    """
    Store the GO2 video track. We'll attach it to a PC RTCPeerConnection
    when the PC calls /offer. We’ll relay via MediaRelay for multi-subscriber safety.
    """
    logger.info(f"received go2 video track, {track=}")
    if state.go2_video_track is not None:
        state.go2_video_track.stop()

    # async def on_video_track(track: MediaStreamTrack):
    #     logger.info(f"got video track: {track}")
    #     global display_task
    #     if display_task is not None:
    #         display_task.cancel()
    #         await display_task

    # state.display_task = asyncio.create_task(display_video(track))
    state.go2_video_track = track

class ConnectArgs(BaseModel):
    robot_ip: str = "192.168.12.1"
    robot_num: int = 0
    token: str = ""
    topics_to_subscribe_to: list[str] = TOPICS_TO_SUBSCRIBE_TO

class ConnectReply(BaseModel):
    robot_ip: str

class UpdateSubscriptionsArgs(BaseModel):
    topics: list[str]

async def _force_disconnect_all(state: WebRTCRelayAppState):
    """Internal helper to force disconnect all connections. Used for idle timeout and user switching."""
    # Stop idle timeout monitoring
    if state.idle_timeout_task:
        state.idle_timeout_task.cancel()
        try:
            await state.idle_timeout_task
        except asyncio.CancelledError:
            pass
        state.idle_timeout_task = None
    
    # Stop stats monitoring
    if state.go2_stats_monitor:
        await state.go2_stats_monitor.stop()
        state.go2_stats_monitor = None
    
    # Close relay peer connection
    await state.close_rtc_relay_connection()
    
    # Close GO2 connection
    if state.go2:
        await state.go2.disconnect()
        state.go2 = None
        state.go2_video_track = None
    
    # Clear user tracking
    state.current_user_id = None
    state.current_user_email = None
    state.last_activity_time = None

@router.post("/connect", response_model=ConnectReply)
async def connect(
    args: ConnectArgs, 
    state: WebRTCRelayAppState = Depends(get_app_state),
    user: dict = Depends(verify_firebase_token)
):
    """
    Connect Raspberry Pi to the GO2 over the AP subnet using your Go2Connection.
    Stores the connection and (optional) video track in app.state.
    
    Enforces single-user access: only one user can be connected at a time.
    Auto-disconnects idle users after 5 minutes of inactivity.
    """
    user_id = user.get("uid")
    user_email = user.get("email")
    
    # Get idle timeout from environment (default: 5 minutes = 300 seconds)
    idle_timeout_seconds = float(os.getenv("RELAY_IDLE_TIMEOUT_SECONDS", "300.0"))
    
    # Check if another user is already connected
    if state.current_user_id is not None:
        if state.current_user_id == user_id:
            # Same user reconnecting - just update activity and allow
            state.update_activity()
            logger.info(f"User {user_id} ({user_email}) already connected, updating activity timestamp")
            # If GO2 connection exists, we're done (don't reconnect)
            if state.go2 is not None:
                return ConnectReply(robot_ip=args.robot_ip)
        else:
            # Different user - check if current user is idle
            if state.last_activity_time:
                idle_time = time.time() - state.last_activity_time
                if idle_time < idle_timeout_seconds:
                    # Current user is still active
                    raise StateException(
                        f"Another user ({state.current_user_email}) is currently connected and active. "
                        f"Please wait until they disconnect or become idle (after {idle_timeout_seconds/60:.1f} minutes)."
                    )
                else:
                    # Current user is idle, disconnect them automatically
                    logger.info(
                        f"Disconnecting idle user {state.current_user_id} ({state.current_user_email}) "
                        f"after {idle_time:.1f}s of inactivity to allow {user_id} ({user_email})"
                    )
                    # Force disconnect the idle user
                    await state._force_disconnect_idle_user()
            else:
                # No activity time recorded, disconnect anyway
                logger.warning(f"Disconnecting existing connection without activity time to allow {user_id}")
                await _force_disconnect_all(state)
    
    # Block if GO2 is still connected (shouldn't happen after disconnect, but safety check)
    if state.go2 is not None:
        logger.warning("GO2 connection still exists after user change, forcing disconnect")
        await _force_disconnect_all(state)

    go2 = Go2Connection(
        robot_ip=args.robot_ip,
        robot_num=args.robot_num,
        token=args.token,
        on_open=lambda : logger.info("GO2 data channel open"),
        on_message=lambda robot_data: _on_go2_message(state, robot_data),
        on_validated=lambda _robot_id:_on_go2_validated(state, args.topics_to_subscribe_to),
        on_video_frame=lambda track, rn: _on_go2_video_track(state, track, rn),
        decode_lidar=False,
        decode_message=False,
    )
    try:
        await go2.connect()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GO2 connect failed: {e}")

    state.go2 = go2
    
    # Set current user and update activity
    state.current_user_id = user_id
    state.current_user_email = user_email
    state.update_activity()
    
    # Start idle timeout monitoring
    await state.start_idle_timeout_monitor(timeout_seconds=idle_timeout_seconds)
    
    # Wait for WebRTC connection to be fully established before starting stats monitoring
    # This ensures stats collection will have meaningful data
    max_wait_time = 10.0  # Maximum wait time in seconds
    wait_interval = 0.1   # Check every 100ms
    waited = 0.0
    while go2.pc.connectionState != "connected" and waited < max_wait_time:
        await asyncio.sleep(wait_interval)
        waited += wait_interval
    
    if go2.pc.connectionState != "connected":
        logger.warning(f"GO2 connection state is {go2.pc.connectionState} after {waited:.1f}s, starting stats monitor anyway")
    else:
        logger.info(f"GO2 connection established (state: {go2.pc.connectionState}), starting stats monitor")
    
    # Start WebRTC stats monitoring for GO2→Relay connection
    enable_stats = os.getenv("ENABLE_WEBRTC_STATS", "false").lower() in ("true", "1", "yes")
    debug_stats = os.getenv("DEBUG_WEBRTC_STATS", "false").lower() in ("true", "1", "yes")
    
    if enable_stats:
        go2_stats_monitor = WebRTCStatsMonitor("GO2→RELAY", go2.pc, debug=debug_stats)
        await go2_stats_monitor.start(interval_seconds=5.0)
        state.go2_stats_monitor = go2_stats_monitor
    else:
        logger.info("WebRTC stats monitoring disabled")
    
    logger.info(f"User {user_id} ({user_email}) successfully connected to GO2")
    return ConnectReply(robot_ip=args.robot_ip)


class DisconnectArgs(BaseModel):
    pass

class DisconnectReply(BaseModel):
    pass


@router.post("/disconnect", response_model=DisconnectReply)
async def disconnect(
    _args: DisconnectArgs, 
    state: WebRTCRelayAppState = Depends(get_app_state),
    user: dict = Depends(verify_firebase_token)
):
    """
    Disconnect from GO2 and tear down any existing PC session.
    Clears user tracking and stops idle timeout monitoring.
    """
    user_id = user.get("uid")
    user_email = user.get("email")
    
    # Verify user is authorized to disconnect (must be the connected user)
    if state.current_user_id is not None and state.current_user_id != user_id:
        logger.warning(
            f"User {user_id} ({user_email}) attempted to disconnect, "
            f"but current connected user is {state.current_user_id} ({state.current_user_email})"
        )
        # Still allow disconnect to prevent stuck connections
    
    logger.info(f"User {user_id} ({user_email}) disconnecting")
    
    # Stop idle timeout monitoring
    if state.idle_timeout_task:
        state.idle_timeout_task.cancel()
        try:
            await state.idle_timeout_task
        except asyncio.CancelledError:
            pass
        state.idle_timeout_task = None
    
    # Stop stats monitoring
    if state.go2_stats_monitor:
        await state.go2_stats_monitor.stop()
        state.go2_stats_monitor = None
    
    # Close PC side first
    await state.close_rtc_relay_connection()

    # Close GO2
    if state.go2:
        await state.go2.disconnect()
        state.go2 = None
        state.go2_video_track = None

    # Clear user tracking
    state.current_user_id = None
    state.current_user_email = None
    state.last_activity_time = None
    
    logger.info(f"User {user_id} ({user_email}) successfully disconnected")
    return DisconnectReply()

@router.post("/update-subscriptions", response_model=DisconnectReply)
async def update_subscriptions(
    args: UpdateSubscriptionsArgs,
    state: WebRTCRelayAppState = Depends(get_app_state),
    user: dict = Depends(verify_firebase_token)
):
    """
    Update topic subscriptions for the current GO2 connection.
    This will unsubscribe from old topics and subscribe to new ones.
    Updates activity timestamp.
    """
    # Update activity on subscription changes
    state.update_activity()
    
    if state.go2 is None:
        raise StateException("Not connected to GO2. Call /connect first.")
    
    if not state.go2.data_channel or state.go2.data_channel.readyState != "open":
        raise StateException("GO2 data channel is not open")
    
    try:
        # Get current and new topics as sets for efficient comparison
        current_topics = set(getattr(state, 'subscribed_topics', set()))
        new_topics = set(args.topics)
        
        # Only unsubscribe from topics not in new list
        topics_to_unsubscribe = current_topics - new_topics
        # Only subscribe to topics not already subscribed
        topics_to_subscribe = new_topics - current_topics
        
        # Helper function to send subscription messages
        def send_subscription(action: str, topic: str):
            state.go2.data_channel.send(
                json.dumps({"type": action, "topic": topic})
            )
            action_past = "unsubscribed from" if action == "unsubscribe" else "subscribed to"
            logger.info(f"{action_past} topic: {topic}")
        
        # Unsubscribe from removed topics
        for topic in topics_to_unsubscribe:
            send_subscription("unsubscribe", topic)
        
        # Subscribe to new topics
        for topic in topics_to_subscribe:
            send_subscription("subscribe", topic)
        
        # Update state
        state.subscribed_topics = new_topics
        
        return DisconnectReply()
    except Exception as e:
        logger.error(f"Error updating subscriptions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update subscriptions: {e}")

@router.get("/subscriptions")
async def get_subscriptions(
    state: WebRTCRelayAppState = Depends(get_app_state),
    user: dict = Depends(verify_firebase_token)
):
    """Get the list of topics currently subscribed to."""
    if state.go2 is None:
        raise StateException("Not connected to GO2. Call /connect first.")
    
    return {
        "subscribed_topics": list(state.subscribed_topics)
    }
