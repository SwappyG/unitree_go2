import logging
import typing as t
from uuid import UUID

import just_robots_firebase_client.firebase_types as fbt
from fastapi import APIRouter, Depends, Query

from just_robots.webrtc_relay.webrtc_dependencies import get_app_state, get_user
from just_robots.webrtc_relay.webrtc_relay import WebRTCRelay
from just_robots.webrtc_relay.webrtc_relay_types import (
    AddSubscriptionArgs,
    AddSubscriptionReply,
    ConnectArgs,
    ConnectReply,
    DisconnectArgs,
    DisconnectReply,
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
    logger.info(f"User {user.email} requested to connect to GO2 at {args.robot_ip}")
    await state.connect_to_go2(
        robot_ip=args.robot_ip,
        robot_num=args.robot_num,
        token=args.token,
        reconnect=args.reconnect_to_go2,
    )

    logger.info(f"Connected to GO2 at {args.robot_ip}, requested by user {user.email}")
    return ConnectReply(robot_ip=args.robot_ip)


@router.post("/disconnect", response_model=DisconnectReply)
async def disconnect(
    _args: DisconnectArgs,
    state: t.Annotated[WebRTCRelay, Depends(get_app_state)],
    user: t.Annotated[fbt.DecodedToken, Depends(get_user)],
):
    logger.info(f"User {user.email} requested to disconnect from GO2")
    await state.disconnect_from_go2()
    logger.info(f"Disconnected from GO2, requested by user {user.email}")
    return DisconnectReply()


@router.post("/add-subscription", response_model=AddSubscriptionReply)
async def add_subscription(
    args: AddSubscriptionArgs,
    state: t.Annotated[WebRTCRelay, Depends(get_app_state)],
    user: t.Annotated[fbt.DecodedToken, Depends(get_user)],
):
    """
    Add a topic subscription for a specific connection.
    The connection_id is returned from the /webrtc/offer endpoint.
    """
    logger.info(
        f"User {user.email} adding subscription to {args.topic} "
        f"for connection {args.connection_id}"
    )
    await state.add_sub_for_connection(args.connection_id, args.topic)
    return AddSubscriptionReply()


@router.post("/remove-subscription", response_model=RemoveSubscriptionReply)
async def remove_subscription(
    args: RemoveSubscriptionArgs,
    state: t.Annotated[WebRTCRelay, Depends(get_app_state)],
    user: t.Annotated[fbt.DecodedToken, Depends(get_user)],
):
    """
    Remove a topic subscription from a specific connection.
    The connection_id is returned from the /webrtc/offer endpoint.
    """
    logger.info(
        f"User {user.email} removing subscription from {args.topic} "
        f"for connection {args.connection_id}"
    )
    await state.remove_sub_from_connection(args.connection_id, args.topic)
    return RemoveSubscriptionReply()


@router.get("/subscriptions", response_model=GetSubscriptionsReply)
async def get_subscriptions(
    connection_id: t.Annotated[
        UUID, Query(description="Connection ID from /webrtc/offer")
    ],
    state: t.Annotated[WebRTCRelay, Depends(get_app_state)],
    user: t.Annotated[fbt.DecodedToken, Depends(get_user)],
):
    """Get the list of topics currently subscribed to for a specific connection."""
    logger.info(
        f"User {user.email} getting subscriptions for connection {connection_id}"
    )
    return GetSubscriptionsReply(
        subscribed_topics=list(await state.get_subs_for_connection(connection_id))
    )
