from aiortc import RTCPeerConnection, RTCDataChannel, RTCConfiguration, RTCSessionDescription  # type: ignore
from fastapi import Depends, APIRouter
import logging
from pydantic import BaseModel
import typing as t
import os

from go2_robot_sdk.webrtc_relay.webrtc_relay_app_state import get_app_state, WebRTCRelayAppState
from go2_robot_sdk.webrtc_relay.webrtc_relay_exceptions import StateException
from go2_robot_sdk.webrtc_relay.webrtc_stats_monitor import WebRTCStatsMonitor
from go2_robot_sdk.webrtc_relay.firebase_auth_server import verify_firebase_token
from go2_robot_sdk.webrtc_relay.ice_server_config import get_rtc_configuration, get_ice_servers_list

logger = logging.getLogger(__name__)
router = APIRouter()

class OfferArgs(BaseModel):
    sdp: str
    type: str

class OfferReply(BaseModel):
    sdp: str
    type: str


def _on_datachannel_message(state: WebRTCRelayAppState, message: t.Any):
    """handler for messages inbound from relay'ed webrtc connection"""
    logger.debug(f"relay rtc data channel got {message=}")
    # Update activity timestamp on any message from client
    state.update_activity()
    
    # take reference to go2 in case its modified later
    # go2: Go2Connection = state.go2
    if not state.go2:
        logger.warning(f"go2 has no data_channel connected to send message to")
        return
    
    if isinstance(message, str):
        # Assume this is a json string and forward it
        state.go2.publish_json_str(message)
        return
    
    logger.warning(f"Got unexpected data type in datachannel: {type(message)!s}, {message=}")
    return


def _on_datachannel(state: WebRTCRelayAppState, channel: RTCDataChannel):  # pyright: ignore[reportUnusedFunction]
    logger.info(f"relay_rtc_connection received data channel, {channel.label=}")
    state.relay_rtc_data_channel = channel

    channel.on("open", lambda *_args: logger.info("WebRTC relay data channel connection open"))
    channel.on("message", lambda message: _on_datachannel_message(state, message))
    

@router.post("/offer", response_model=OfferReply)
async def offer(
    sdp: OfferArgs,
    state: t.Annotated[WebRTCRelayAppState, Depends(get_app_state)],
    user: t.Annotated[dict[str, t.Any], Depends(verify_firebase_token)]
):
    """Handle WebRTC offer from client. Updates activity timestamp."""
    user_id = user.get("uid")
    
    # Verify user is authorized (must be the connected user)
    if state.current_user_id is not None and state.current_user_id != user_id:
        raise StateException(
            f"User {user_id} is not authorized. Currently connected user is {state.current_user_id}."
        )
    
    # Update activity on offer
    state.update_activity()

    if state.go2 is None:
        raise StateException("connection to the go2 hasn't been established yet, call /connect first")
    
    await state.close_rtc_relay_connection()

    try:
        logger.info(f"creating new rtc connection to relay data from go2 to caller")
        # Get ICE server configuration from environment variables
        rtc_config = get_rtc_configuration()
        ice_servers = get_ice_servers_list()
        # Safely extract URLs for logging
        ice_server_urls = []
        for s in ice_servers:
            try:
                if isinstance(s, dict):
                    url = s.get('urls', 'unknown')
                    # Handle case where urls might be a list
                    if isinstance(url, list):
                        url = url[0] if url else 'unknown'
                else:
                    # Handle case where it might be an object with urls attribute
                    url = getattr(s, 'urls', 'unknown')
                ice_server_urls.append(str(url))
            except Exception as e:
                logger.warning(f"Error extracting ICE server URL: {e}, server: {s}")
                ice_server_urls.append('unknown')
        logger.info(f"Using ICE servers: {ice_server_urls}")
        new_relay_peer_connection = RTCPeerConnection(configuration=rtc_config)
        state.relay_rtc_peer_connection = new_relay_peer_connection

        # Accept PC-created data channel
        new_relay_peer_connection.on("datachannel", lambda data: _on_datachannel(state, data))

        # Attach GO2 video (if present)
        if state.go2_video_track:
            logger.info(f"adding go2 video track to new relay connection")
            new_relay_peer_connection.addTrack(state.media_relay.subscribe(state.go2_video_track))

        # SDP handshake
        logger.info(f"relay RTC setting remote description")
        await new_relay_peer_connection.setRemoteDescription(RTCSessionDescription(sdp=sdp.sdp, type=sdp.type))
        logger.info(f"relay RTC creating answer")
        answer = await new_relay_peer_connection.createAnswer()
        
        logger.info(f"relay RTC setting local description")

        # NOTE: in the newer aiortc versions, answer is always a type. But here in 1.9, it can be None
        await new_relay_peer_connection.setLocalDescription(answer)  # type: ignore

        # Start WebRTC stats monitoring for relay→client connection
        # This monitors RELAY→CLIENT (relay sending to client)
        enable_stats = os.getenv("ENABLE_WEBRTC_STATS", "false").lower() in ("true", "1", "yes")
        debug_stats = os.getenv("DEBUG_WEBRTC_STATS", "false").lower() in ("true", "1", "yes")
        
        if enable_stats:
            stats_monitor_relay_to_client = WebRTCStatsMonitor("RELAY→CLIENT", new_relay_peer_connection, debug=debug_stats)
            await stats_monitor_relay_to_client.start(interval_seconds=5.0)
            state.relay_stats_monitor = stats_monitor_relay_to_client
            
            # Also monitor CLIENT→RELAY (relay receiving from client)
            # The relay's peer connection receives from client, so we can get RTT from remote-inbound-rtp
            stats_monitor_client_to_relay = WebRTCStatsMonitor("CLIENT→RELAY", new_relay_peer_connection, debug=debug_stats)
            await stats_monitor_client_to_relay.start(interval_seconds=5.0)
            state.client_to_relay_stats_monitor = stats_monitor_client_to_relay
        else:
            logger.info("WebRTC stats monitoring disabled")

        # Re-trigger video to push fresh SPS/PPS for new subscriber
        try:
            if state.go2:
                state.go2.publish("", "on", "vid")
        except Exception as exception:
            logger.warning("Could not re-trigger video:", exception)
        
        return OfferReply(
            sdp=new_relay_peer_connection.localDescription.sdp,
            type=new_relay_peer_connection.localDescription.type,
        )
    
    except Exception as exception:
        logger.warning(f"Failed to create relay rtc sessiondescriptionprotocol (SDP). {exception=}")
        raise StateException(f"Failed to create relay rtc sessiondescriptionprotocol (SDP)") from exception
