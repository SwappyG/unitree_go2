"""
Firebase authentication helper for WebRTC relay client.

This module provides functionality to authenticate with Firebase and obtain ID tokens
for use in API requests to the relay server.
"""

import json
import logging
from pathlib import Path

import pyrebase

logger = logging.getLogger(__name__)


class FirebaseAuthManager:
    """
    Manages Firebase authentication and token retrieval.

    Supports multiple authentication methods:
    1. Direct ID token (user provides token directly)
    2. Firebase Admin SDK (for service account authentication)
    3. Pyrebase4 (for user authentication with email/password)
    """

    def __init__(
        self,
        firebase_client_config_filepath: Path,
        firebase_email: str,
        firebase_password: str,
    ):
        """
        Initialize Firebase authentication manager.

        Args:
            firebase_id_token: Direct Firebase ID token to use (highest priority)
            firebase_config_path: Path to Firebase service account JSON file
            firebase_api_key: Firebase API key for user authentication
            firebase_email: Email for user authentication
            firebase_password: Password for user authentication
        """
        self._firebase_client_config_filepath = firebase_client_config_filepath
        self._firebase_email = firebase_email
        self._firebase_password = firebase_password
        with Path.open(firebase_client_config_filepath, encoding="utf-8") as ff:
            self._pyrebase_app = pyrebase.initialize_app(json.load(ff))
            self._pyrebase_auth = self._pyrebase_app.auth()
            self._user = self._pyrebase_auth.sign_in_with_email_and_password(
                email=self._firebase_email,
                password=self._firebase_password,
            )

    def get_id_token(self) -> str:
        return self._user["idToken"]

    async def refresh_token(self) -> str | None:
        """
        Refresh the Firebase ID token if needed.

        Returns:
            New Firebase ID token, or None if refresh failed
        """
        self._user = self._pyrebase_auth.refresh(self._user["refreshToken"])
        return self._user["idToken"]

    def get_auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._user['idToken']}"}
