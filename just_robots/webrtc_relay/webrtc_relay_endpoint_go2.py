import logging
import typing as t

import just_robots_firebase_client.firebase_types as fbt
from fastapi import APIRouter, Depends

from just_robots.webrtc_relay.webrtc_dependencies import get_app_state, get_user
from just_robots.webrtc_relay.webrtc_relay import WebRTCRelay
from just_robots.webrtc_relay.webrtc_relay_types import (
    AddSubscriptionArgs,
    AddSubscriptionReply,
    ConnectArgs,
    ConnectReply,
    DisconnectArgs,
    DisconnectReply,
    GetSubscriptionsArgs,
    GetSubscriptionsReply,
    RemoveSubscriptionArgs,
    RemoveSubscriptionReply,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/connect", response_model=ConnectReply)
async def connect(
    args: ConnectArgs,
    state: t.Annotated[WebRTCRelay, Depends(get_app_state)],
    user: t.Annotated[fbt.DecodedToken, Depends(get_user)],
):
    await state.connect_to_go2(
        robot_ip=args.robot_ip,
        robot_num=args.robot_num,
        token=args.token,
        reconnect=args.reconnect_to_go2,
    )

    logger.info(f"User {user.uid} ({user.email}) successfully connected to GO2")
    return ConnectReply(robot_ip=args.robot_ip)


@router.post("/disconnect", response_model=DisconnectReply)
async def disconnect(
    _args: DisconnectArgs,
    state: t.Annotated[WebRTCRelay, Depends(get_app_state)],
    _user: t.Annotated[fbt.DecodedToken, Depends(get_user)],
):
    await state.disconnect_from_go2()
    return DisconnectReply()


@router.post("/add-subscription", response_model=AddSubscriptionReply)
async def add_subscription(
    args: AddSubscriptionArgs,
    state: t.Annotated[WebRTCRelay, Depends(get_app_state)],
    user: t.Annotated[fbt.DecodedToken, Depends(get_user)],
):
    """
    Update topic subscriptions for the current GO2 connection.
    This will unsubscribe from old topics and subscribe to new ones.
    Updates activity timestamp.
    """
    await state.add_sub_for_peer(user.uid, args.topic)
    return AddSubscriptionReply()


@router.post("/remove-subscription", response_model=RemoveSubscriptionReply)
async def remove_subscription(
    args: RemoveSubscriptionArgs,
    state: t.Annotated[WebRTCRelay, Depends(get_app_state)],
    user: t.Annotated[fbt.DecodedToken, Depends(get_user)],
):
    await state.remove_sub_from_peer(user.uid, args.topic)
    return RemoveSubscriptionReply()


@router.get("/subscriptions", response_model=GetSubscriptionsReply)
async def get_subscriptions(
    _args: GetSubscriptionsArgs,
    state: t.Annotated[WebRTCRelay, Depends(get_app_state)],
    user: t.Annotated[fbt.DecodedToken, Depends(get_user)],
):
    """Get the list of topics currently subscribed to."""
    return GetSubscriptionsReply(
        subscribed_topics=list(await state.get_subs_for_peer(user.uid))
    )
