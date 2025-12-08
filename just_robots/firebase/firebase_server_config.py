"""
Firebase authentication for WebRTC relay server.

This module provides server-side Firebase token verification.
Any user with a valid Firebase token (exists in Firebase) is authorized to access the relay endpoints.
"""

import logging
from pathlib import Path

import firebase_admin
from fastapi.security import HTTPBearer
from firebase_admin import App, credentials

logger = logging.getLogger(__name__)

# Security scheme for Bearer token
security = HTTPBearer()

# Optional security scheme (doesn't raise error if token is missing)
security_optional = HTTPBearer(auto_error=False)


class FirebaseAuthConfig:
    """Configuration for Firebase authentication on the server."""

    def __init__(
        self,
        firebase_config_path: Path | None = None,
        authorized_users: list[str] | None = None,
    ):
        """
        Initialize Firebase authentication configuration.

        Args:
            firebase_config_path: Path to Firebase service account JSON file
            authorized_users: List of authorized user UIDs or emails
            enabled: Whether Firebase auth is enabled (default: True)
        """
        self.firebase_config_path = firebase_config_path
        self.authorized_users = set(authorized_users or [])
        self._firebase_app = self._initialize_firebase_admin()

    def _initialize_firebase_admin(self) -> App:
        """Initialize Firebase Admin SDK with service account credentials."""
        if self.firebase_config_path:
            config_path = Path(self.firebase_config_path)
            logger.info(f"Attempting to initialize Firebase with config: {config_path=}")
            try:
                cred = credentials.Certificate(str(config_path))
            except OSError:  # aliased from IOError after Python 3.3
                logger.warning(
                    f"Firebase config file not found: "
                    f"{config_path}, {Path.cwd()=}, {config_path.resolve()=}"
                )
                raise
            # Check if app already exists to avoid re-initialization
            try:
                return firebase_admin.get_app()
            except ValueError:
                return firebase_admin.initialize_app(cred)

        logger.warning("No Firebase config path provided, trying default credentials")
        # Try to use default credentials (e.g., from environment)
        try:
            return firebase_admin.get_app()
        except ValueError:
            # Try to initialize with default credentials
            try:
                return firebase_admin.initialize_app()
            except ValueError:
                logger.warning(
                    "Failed to initialize with default credentials. Please "
                    "provide FIREBASE_CONFIG_PATH environment variable"
                )
                raise

    def is_user_authorized(self, user_uid: str, user_email: str | None = None) -> bool:
        """
        Check if a user is authorized.

        Since we only check Firebase authentication, any user with a valid token
        is considered authorized (they exist in Firebase).

        Args:
            user_uid: Firebase user UID
            user_email: Firebase user email (optional)

        Returns:
            True (all authenticated Firebase users are authorized)
        """
        _ = user_email
        _ = user_uid

        # If token is valid, user exists in Firebase and is authorized
        return True
