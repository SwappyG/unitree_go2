"""
Firebase authentication for WebRTC relay server.

This module provides server-side Firebase token verification.
Any user with a valid Firebase token (exists in Firebase) is authorized to access the relay endpoints.
"""
import os
import logging
from typing import Optional
from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pathlib import Path

logger = logging.getLogger(__name__)

# Try to import Firebase Admin SDK
try:
    import firebase_admin
    from firebase_admin import credentials, auth
    FIREBASE_ADMIN_AVAILABLE = True
except ImportError:
    FIREBASE_ADMIN_AVAILABLE = False
    firebase_admin = None
    auth = None
    credentials = None

# Security scheme for Bearer token
security = HTTPBearer()

# Optional security scheme (doesn't raise error if token is missing)
security_optional = HTTPBearer(auto_error=False)


class FirebaseAuthConfig:
    """Configuration for Firebase authentication on the server."""
    
    def __init__(
        self,
        firebase_config_path: Optional[str] = None,
        authorized_users: Optional[list[str]] = None,
        enabled: bool = True,
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
        self.enabled = enabled
        self._firebase_app = None
        
        # Initialize Firebase Admin if config path is provided
        if firebase_config_path and FIREBASE_ADMIN_AVAILABLE:
            self._initialize_firebase_admin()
        elif enabled and not FIREBASE_ADMIN_AVAILABLE:
            logger.warning(
                "Firebase Admin SDK not available. Install with: pip install firebase-admin. "
                "Authentication will be disabled."
            )
            self.enabled = False
    
    def _initialize_firebase_admin(self):
        """Initialize Firebase Admin SDK with service account credentials."""
        if not FIREBASE_ADMIN_AVAILABLE:
            logger.error("Firebase Admin SDK not available. Install with: pip install firebase-admin")
            self.enabled = False
            return
        
        try:
            if self.firebase_config_path:
                config_path = Path(self.firebase_config_path)
                logger.info(f"Attempting to initialize Firebase with config: {config_path}")
                if not config_path.exists():
                    logger.error(f"Firebase config file not found: {config_path}")
                    logger.error(f"Current working directory: {os.getcwd()}")
                    logger.error(f"Absolute path would be: {config_path.resolve()}")
                    self.enabled = False
                    return
                
                logger.info(f"Loading Firebase credentials from: {config_path}")
                cred = credentials.Certificate(str(config_path))
                # Check if app already exists to avoid re-initialization
                try:
                    self._firebase_app = firebase_admin.get_app()
                    logger.info("Using existing Firebase Admin app")
                except ValueError:
                    self._firebase_app = firebase_admin.initialize_app(cred)
                    logger.info("Firebase Admin SDK initialized successfully")
            else:
                logger.warning("No Firebase config path provided, trying default credentials")
                # Try to use default credentials (e.g., from environment)
                try:
                    self._firebase_app = firebase_admin.get_app()
                    logger.info("Using existing Firebase Admin app")
                except ValueError:
                    # Try to initialize with default credentials
                    try:
                        self._firebase_app = firebase_admin.initialize_app()
                        logger.info("Firebase Admin SDK initialized with default credentials")
                    except Exception as default_error:
                        logger.error(f"Failed to initialize with default credentials: {default_error}")
                        logger.error("Please provide FIREBASE_CONFIG_PATH environment variable")
                        self.enabled = False
        except Exception as e:
            logger.error(f"Failed to initialize Firebase Admin SDK: {e}")
            logger.exception("Full error details:")
            self.enabled = False
    
    def is_user_authorized(self, user_uid: str, user_email: Optional[str] = None) -> bool:
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
        # If token is valid, user exists in Firebase and is authorized
        return True


# Global Firebase auth config (will be initialized at startup)
_firebase_auth_config: Optional[FirebaseAuthConfig] = None


def initialize_firebase_auth(
    firebase_config_path: Optional[str] = None,
    authorized_users: Optional[list[str]] = None,
    enabled: bool = True,
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
    global _firebase_auth_config
    
    # Get from environment variables if not provided
    if not firebase_config_path:
        firebase_config_path = os.getenv("FIREBASE_CONFIG_PATH")
    
    if not authorized_users:
        # Try to get from environment variable (comma-separated)
        auth_users_env = os.getenv("FIREBASE_AUTHORIZED_USERS")
        if auth_users_env:
            authorized_users = [uid.strip() for uid in auth_users_env.split(",")]
    
    # Check if auth should be enabled
    if enabled is None:
        enabled = os.getenv("FIREBASE_AUTH_ENABLED", "true").lower() in ("true", "1", "yes")
    
    _firebase_auth_config = FirebaseAuthConfig(
        firebase_config_path=firebase_config_path,
        authorized_users=authorized_users,
        enabled=enabled,
    )
    
    return _firebase_auth_config


def get_firebase_auth_config() -> Optional[FirebaseAuthConfig]:
    """Get the global Firebase auth configuration."""
    return _firebase_auth_config


async def verify_firebase_token(
    credentials: HTTPAuthorizationCredentials = Security(security)
) -> dict:
    """
    FastAPI dependency to verify Firebase ID token and check authorization.
    
    Args:
        credentials: HTTP Bearer token credentials from request header
    
    Returns:
        Dictionary with user information (uid, email, etc.)
    
    Raises:
        HTTPException: If token is invalid or user is not authorized
    """
    config = get_firebase_auth_config()
    
    # If auth is disabled, allow all requests
    if not config or not config.enabled:
        logger.debug("Firebase authentication is disabled")
        return {"uid": "anonymous", "email": None, "authenticated": False}
    
    if not FIREBASE_ADMIN_AVAILABLE:
        raise HTTPException(
            status_code=500,
            detail="Firebase Admin SDK not available. Server misconfiguration."
        )
    
    # Check if Firebase app was initialized
    if not hasattr(config, '_firebase_app') or config._firebase_app is None:
        logger.error("Firebase Admin SDK not initialized! Check server logs for initialization errors.")
        raise HTTPException(
            status_code=500,
            detail="Firebase Admin SDK not initialized. Check server configuration and logs."
        )
    
    token = credentials.credentials
    
    try:
        # Verify the Firebase ID token
        decoded_token = auth.verify_id_token(token)
        
        user_uid = decoded_token.get("uid")
        user_email = decoded_token.get("email")
        
        if not user_uid:
            raise HTTPException(
                status_code=401,
                detail="Invalid token: missing user UID"
            )
        
        # If token is valid, user is authenticated and authorized (exists in Firebase)
        logger.info(f"Authenticated user: {user_uid} ({user_email})")
        
        return {
            "uid": user_uid,
            "email": user_email,
            "authenticated": True,
            "token_data": decoded_token,
        }
    
    except HTTPException:
        # Re-raise HTTPExceptions (like 403 Forbidden) without modification
        raise
    except ValueError as e:
        # Invalid token format
        logger.warning(f"Invalid Firebase token: {e}")
        raise HTTPException(
            status_code=401,
            detail=f"Invalid authentication token: {str(e)}"
        )
    except Exception as e:
        # Check if it's a Firebase-specific error
        error_str = str(e).lower()
        if "invalid" in error_str or "expired" in error_str or "revoked" in error_str:
            logger.warning(f"Firebase token verification error: {e}")
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired authentication token"
            )
        else:
            logger.error(f"Unexpected error during token verification: {e}")
            raise HTTPException(
                status_code=500,
                detail="Authentication service error"
            )


# Optional dependency - allows endpoints to work with or without auth
async def verify_firebase_token_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_optional)
) -> dict:
    """
    Optional Firebase token verification - doesn't require auth if disabled.
    
    Args:
        credentials: HTTP Bearer token credentials (optional)
    
    Returns:
        Dictionary with user information
    """
    config = get_firebase_auth_config()
    
    # If auth is disabled or no credentials provided, return anonymous
    if not config or not config.enabled or not credentials:
        return {"uid": "anonymous", "email": None, "authenticated": False}
    
    # Otherwise, verify the token
    return await verify_firebase_token(credentials)

