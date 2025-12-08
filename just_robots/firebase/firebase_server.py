"""
Firebase authentication for WebRTC relay server.

This module provides server-side Firebase token verification.
Any user with a valid Firebase token (exists in Firebase) is authorized to access the relay endpoints.
"""

import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth
from firebase_admin.exceptions import FirebaseError
from pydantic import BaseModel

from just_robots.firebase.firebase_server_config import FirebaseAuthConfig
from just_robots.utils.settings import get_just_robots_settings

logger = logging.getLogger(__name__)
# Security scheme for Bearer token
security = HTTPBearer()

# Optional security scheme (doesn't raise error if token is missing)
security_optional = HTTPBearer(auto_error=False)


class FirebaseUser(BaseModel):
    uid: str
    email: str
    authenticated: bool
    token_data: dict[str, Any]


def initialize_firebase_auth(
    firebase_config_path: Path | None = None,
    authorized_users: list[str] | None = None,
) -> FirebaseAuthConfig:
    """
    Initialize Firebase authentication for the server.

    Args:
        firebase_config_path: Path to Firebase service account JSON file
        authorized_users: List of authorized user UIDs or emails
        enabled: Whether Firebase auth is enabled

    Returns:
        FirebaseAuthConfig instance
    """
    just_robots_settings = get_just_robots_settings()
    # Get from environment variables if not provided
    if firebase_config_path is None:
        firebase_config_path = just_robots_settings.FIREBASE_CONFIG_PATH
        if firebase_config_path is None:
            raise ValueError("no config found for firebase")

    if not authorized_users:
        # Try to get from environment variable (comma-separated)
        users = get_just_robots_settings().FIREBASE_AUTHORIZED_USERS
        authorized_users = users if users is not None else []

    firebase_auth_config = FirebaseAuthConfig(
        firebase_config_path=firebase_config_path,
        authorized_users=authorized_users,
    )

    return firebase_auth_config


async def verify_firebase_token(
    credentials: Annotated[HTTPAuthorizationCredentials, Security(security)],
    config: FirebaseAuthConfig | None,
) -> FirebaseUser:
    """
    FastAPI dependency to verify Firebase ID token and check authorization.
    If config doesn't exist, all requests are accepted (ie, auth is disabled)
    """
    # If auth is disabled, allow all requests
    if config is None:
        logger.debug("Firebase authentication is disabled")
        return FirebaseUser(uid="anonymous", email="", authenticated=False, token_data={})

    token = credentials.credentials

    # Verify the Firebase ID token
    decoded_token = auth.verify_id_token(token)

    try:
        user_uid = decoded_token.get("uid")
        user_email = decoded_token.get("email")
    except FirebaseError as e:
        raise PermissionError("firebase rejected token") from e

    if not user_uid:
        raise PermissionError("Invalid token: missing user UID")

    # If token is valid, user is authenticated and authorized (exists in Firebase)
    logger.info(f"Authenticated user: {user_uid} ({user_email})")

    return FirebaseUser(
        uid=user_uid,
        email=user_email,
        authenticated=True,
        token_data=decoded_token,
    )
