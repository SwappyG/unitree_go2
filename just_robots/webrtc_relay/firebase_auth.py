"""
Firebase authentication helper for WebRTC relay client.

This module provides functionality to authenticate with Firebase and obtain ID tokens
for use in API requests to the relay server.
"""
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Try to import Firebase libraries
try:
    import firebase_admin
    from firebase_admin import credentials, auth
    FIREBASE_ADMIN_AVAILABLE = True
except ImportError:
    FIREBASE_ADMIN_AVAILABLE = False
    firebase_admin = None
    auth = None
    credentials = None

try:
    import pyrebase4
    PYREBASE_AVAILABLE = True
except ImportError:
    PYREBASE_AVAILABLE = False
    pyrebase4 = None


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
        firebase_id_token: str | None = None,
        firebase_config_path: str | None = None,
        firebase_api_key: str | None = None,
        firebase_email: str | None = None,
        firebase_password: str | None = None,
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
        self._id_token: str | None = firebase_id_token
        self._firebase_config_path = firebase_config_path
        self._firebase_api_key = firebase_api_key
        self._firebase_email = firebase_email
        self._firebase_password = firebase_password
        self._firebase_app = None
        self._pyrebase_app = None
        
        # Initialize Firebase Admin if config path is provided
        if firebase_config_path and FIREBASE_ADMIN_AVAILABLE:
            self._initialize_firebase_admin()
        
        # Initialize Pyrebase if credentials are provided
        if firebase_api_key and firebase_email and firebase_password and PYREBASE_AVAILABLE:
            self._initialize_pyrebase()
    
    def _initialize_firebase_admin(self):
        """Initialize Firebase Admin SDK with service account credentials."""
        if not FIREBASE_ADMIN_AVAILABLE:
            logger.warning("Firebase Admin SDK not available. Install with: pip install firebase-admin")
            return
        
        try:
            config_path = Path(self._firebase_config_path)
            if not config_path.exists():
                logger.error(f"Firebase config file not found: {config_path}")
                return
            
            cred = credentials.Certificate(str(config_path))
            self._firebase_app = firebase_admin.initialize_app(cred)
            logger.info("Firebase Admin SDK initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Firebase Admin SDK: {e}")
    
    def _initialize_pyrebase(self):
        """Initialize Pyrebase for user authentication."""
        if not PYREBASE_AVAILABLE:
            logger.warning("Pyrebase4 not available. Install with: pip install pyrebase4")
            return
        
        try:
            # Note: This is a simplified config. In production, you'd load full Firebase config
            # For now, we'll use the API key to authenticate
            # Full implementation would require project_id, auth_domain, etc.
            logger.warning("Pyrebase initialization requires full Firebase config. Using direct token instead.")
        except Exception as e:
            logger.error(f"Failed to initialize Pyrebase: {e}")
    
    def get_id_token(self) -> str | None:
        """
        Get the current Firebase ID token.
        
        Returns:
            Firebase ID token string, or None if not available
        """
        # If direct token is provided, use it
        if self._id_token:
            return self._id_token
        
        # Try to get token from environment variable
        env_token = os.getenv("FIREBASE_ID_TOKEN")
        if env_token:
            return env_token
        
        # TODO: Implement token refresh for Firebase Admin or Pyrebase
        # For now, return None if no token is available
        return None
    
    async def refresh_token(self) -> str | None:
        """
        Refresh the Firebase ID token if needed.
        
        Returns:
            New Firebase ID token, or None if refresh failed
        """
        # For now, just return the current token
        # Full implementation would check token expiry and refresh if needed
        return self.get_id_token()
    
    def is_authenticated(self) -> bool:
        """Check if authentication is available."""
        return self.get_id_token() is not None


def get_auth_headers(firebase_auth_manager: FirebaseAuthManager | None) -> dict[str, str]:
    """
    Get HTTP headers with Firebase authentication token.
    
    Args:
        firebase_auth_manager: FirebaseAuthManager instance, or None
        
    Returns:
        Dictionary with Authorization header if token is available
    """
    if firebase_auth_manager is None:
        return {}
    
    token = firebase_auth_manager.get_id_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    
    return {}

