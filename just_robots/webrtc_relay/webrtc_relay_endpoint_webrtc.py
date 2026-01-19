import logging
import typing as t

import just_robots_firebase_client.firebase_types as fbt
from fastapi import APIRouter, Depends

from just_robots.webrtc_relay.webrtc_dependencies import get_app_state, get_user
from just_robots.webrtc_relay.webrtc_relay import WebRTCRelay
from just_robots.webrtc_relay.webrtc_relay_types import OfferArgs, OfferReply

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/offer", response_model=OfferReply)
async def offer(
    args: OfferArgs,
    state: t.Annotated[WebRTCRelay, Depends(get_app_state)],
    user: t.Annotated[fbt.DecodedToken, Depends(get_user)],
):
    result = await state.process_peer_offer(
        offer_sdp=args.offer_sdp,
        offer_type=args.offer_type,
        user_firebase_uid=user.uid,
        user_firebase_email=user.email or "",
    )

    if result.sdp is None:
        raise RuntimeError("Failed to create SDP answer")

    return OfferReply(
        offer_sdp=result.sdp.sdp,
        offer_type=result.sdp.type,
        connection_id=result.connection_id,
    )


# # Get ICE server configuration from environment variables
# rtc_config = get_rtc_configuration()
# ice_servers = get_ice_servers_list()
# # Safely extract URLs for logging
# ice_server_urls = []
# for s in ice_servers:
#     try:
#         if isinstance(s, dict):
#             url = s.get("urls", "unknown")
#             # Handle case where urls might be a list
#             if isinstance(url, list):
#                 url = url[0] if url else "unknown"
#         else:
#             # Handle case where it might be an object with urls attribute
#             url = getattr(s, "urls", "unknown")
#         ice_server_urls.append(str(url))
#     except Exception as e:
#         logger.warning(f"Error extracting ICE server URL: {e}, server: {s}")
#         ice_server_urls.append("unknown")
# logger.info(f"Using ICE servers: {ice_server_urls}")
