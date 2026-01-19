"""Protocol for providing authentication tokens."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class FirebaseAuthTokenProvider(Protocol):
    """Protocol for classes that can provide an authentication ID token.

    This allows WebRTCRelayClient to work with any authentication provider
    that implements this interface, including:
    - FirebaseClientAuthenticated (async)
    - FirebaseClientQtAuthenticated (sync, wrapped for async)
    """

    async def get_id_token(self) -> str:
        """Get the current authentication ID token.

        Returns:
            The ID token string for use in Authorization headers.
        """
        ...
