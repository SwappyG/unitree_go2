import typing as t
from logging import getLogger

import just_robots_firebase_client.firebase_types as fbt
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from just_robots.webrtc_relay.webrtc_relay import WebRTCRelay

logger = getLogger(__name__)
bearer_auth = HTTPBearer()


def get_app_state(request: Request) -> WebRTCRelay:
    return request.app.state.state


async def get_user(
    _request: Request,
    credentials: t.Annotated[HTTPAuthorizationCredentials, Security(bearer_auth)],
    state: t.Annotated[WebRTCRelay, Depends(get_app_state)],
) -> fbt.DecodedToken:
    """
    Verify Firebase ID token from Authorization header and return decoded user information.

    Args:
        request: FastAPI request object
        credentials: HTTP Bearer token from Authorization header

    Returns:
        DecodedToken with verified user information

    Raises:
        HTTPException: If token is invalid, expired, or Firebase client is not configured
    """
    id_token = credentials.credentials
    try:
        decoded = await state.firebase_client.decode_token(id_token=id_token)
        return decoded
    except RuntimeError as e:
        logger.warning(f"Firebase client not configured: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Firebase authentication not configured",
        ) from e
    except Exception as e:
        logger.warning(f"Token verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from e
