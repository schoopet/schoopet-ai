"""OAuth manager for state and token management."""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import httpx
from google.cloud import firestore

from .models import OAuthState, OAuthToken
from .secret_manager import SecretManagerClient

logger = logging.getLogger(__name__)

# Firestore collection names
STATES_COLLECTION = "oauth_states"
TOKENS_COLLECTION = "oauth_tokens"

# Google OAuth endpoints
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


class OAuthManager:
    """Manages OAuth state and tokens for Google OAuth flow.

    Responsibilities:
    - Generate and validate CSRF state parameters
    - Exchange authorization codes for tokens
    - Store access tokens in Firestore
    - Store refresh tokens in Secret Manager
    - Refresh expired access tokens
    """

    def __init__(
        self,
        firestore_client: firestore.AsyncClient,
        secret_manager: SecretManagerClient,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        state_ttl_seconds: int = 600,
    ):
        """Initialize OAuth manager.

        Args:
            firestore_client: Async Firestore client instance.
            secret_manager: Secret Manager client for refresh tokens.
            client_id: Google OAuth client ID.
            client_secret: Google OAuth client secret.
            redirect_uri: OAuth callback redirect URI.
            state_ttl_seconds: State parameter TTL in seconds (default: 10 min).
        """
        self._db = firestore_client
        self._secret_manager = secret_manager
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._state_ttl = timedelta(seconds=state_ttl_seconds)
        self._states_collection = self._db.collection(STATES_COLLECTION)
        self._tokens_collection = self._db.collection(TOKENS_COLLECTION)

    def _normalize_phone(self, phone_number: str) -> str:
        """Normalize phone number for consistent document IDs."""
        return phone_number.lstrip("+").replace("-", "").replace(" ", "")

    async def generate_state(self, phone_number: str, feature: str = "calendar") -> str:
        """Generate a new OAuth state parameter for CSRF protection.

        Args:
            phone_number: User's phone number in E.164 format.
            feature: Feature being authorized (calendar, house).

        Returns:
            State ID (UUID) to include in OAuth URL.
        """
        state_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + self._state_ttl

        state = OAuthState(
            state_id=state_id,
            phone_number=phone_number,
            feature=feature,
            created_at=now,
            expires_at=expires_at,
            used=False,
        )

        # Store state in Firestore
        doc_ref = self._states_collection.document(state_id)
        await doc_ref.set(state.to_firestore())

        logger.info(f"Generated OAuth state for phone {phone_number[:4]}****, feature: {feature}")
        return state_id

    async def validate_state(self, state_id: str) -> Tuple[Optional[str], str]:
        """Validate and consume an OAuth state parameter.

        Args:
            state_id: State ID from OAuth callback.

        Returns:
            Tuple of (phone_number, feature). Both None/empty if invalid.
        """
        doc_ref = self._states_collection.document(state_id)
        doc = await doc_ref.get()

        if not doc.exists:
            logger.warning(f"OAuth state not found: {state_id[:8]}...")
            return None, ""

        state = OAuthState.from_firestore(doc.to_dict())

        if not state.is_valid():
            logger.warning(f"OAuth state invalid or expired: {state_id[:8]}...")
            return None, ""

        # Mark state as used (one-time use)
        await doc_ref.update({"used": True})
        logger.info(f"Validated and consumed OAuth state: {state_id[:8]}...")

        return state.phone_number, state.feature

    async def exchange_code_for_tokens(
        self, code: str, scopes: list[str]
    ) -> Tuple[Optional[str], Optional[str], Optional[int], Optional[str]]:
        """Exchange authorization code for access and refresh tokens.

        Args:
            code: Authorization code from OAuth callback.
            scopes: List of OAuth scopes.

        Returns:
            Tuple of (access_token, refresh_token, expires_in, email) or (None, None, None, None) on error.
        """
        async with httpx.AsyncClient() as client:
            try:
                # Exchange code for tokens
                token_response = await client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "code": code,
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "redirect_uri": self._redirect_uri,
                        "grant_type": "authorization_code",
                    },
                )
                token_response.raise_for_status()
                token_data = token_response.json()

                access_token = token_data.get("access_token")
                refresh_token = token_data.get("refresh_token")
                expires_in = token_data.get("expires_in", 3600)

                if not access_token:
                    logger.error("No access token in response")
                    return None, None, None, None

                # Get user email
                userinfo_response = await client.get(
                    GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                userinfo_response.raise_for_status()
                userinfo = userinfo_response.json()
                email = userinfo.get("email")

                logger.info(f"Exchanged code for tokens, email: {email}")
                return access_token, refresh_token, expires_in, email

            except httpx.HTTPStatusError as e:
                logger.error(f"Token exchange failed: {e.response.status_code} - {e.response.text}")
                return None, None, None, None
            except Exception as e:
                logger.error(f"Token exchange error: {e}")
                return None, None, None, None

    async def store_tokens(
        self,
        phone_number: str,
        email: str,
        access_token: str,
        refresh_token: Optional[str],
        expires_in: int,
        feature: str = "calendar",
    ) -> bool:
        """Store OAuth tokens securely.

        Access token goes to Firestore, refresh token to Secret Manager.

        Args:
            phone_number: User's phone number in E.164 format.
            email: Google account email.
            access_token: OAuth access token.
            refresh_token: OAuth refresh token (may be None on refresh).
            expires_in: Token expiration time in seconds.
            feature: Feature associated with token (calendar, house).

        Returns:
            True if successful, False otherwise.
        """
        # Document ID includes feature to allow multiple tokens per user
        normalized_phone = self._normalize_phone(phone_number)
        doc_id = f"{normalized_phone}_{feature}"
        
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=expires_in)

        try:
            # Store access token in Firestore
            token = OAuthToken(
                phone_number=phone_number,
                feature=feature,
                email=email,
                access_token=access_token,
                token_type="Bearer",
                expires_at=expires_at,
                created_at=now,
                updated_at=now,
            )
            doc_ref = self._tokens_collection.document(doc_id)
            await doc_ref.set(token.to_firestore())

            # Store refresh token in Secret Manager (if provided)
            # We append feature to the phone number for the secret key
            if refresh_token:
                # Store as {phone}-{feature} to keep unique
                secret_key = f"{phone_number}-{feature}"
                await self._secret_manager.store_refresh_token(secret_key, refresh_token)

            logger.info(f"Stored tokens for phone {phone_number[:4]}****, feature: {feature}, email: {email}")
            return True

        except Exception as e:
            logger.error(f"Failed to store tokens: {e}")
            return False

    async def get_access_token(self, phone_number: str, feature: str = "calendar") -> Optional[str]:
        """Get a valid access token for a phone number and feature.

        If the token is expired, attempts to refresh it using the stored
        refresh token.

        Args:
            phone_number: User's phone number in E.164 format.
            feature: Feature to get token for (calendar, house).

        Returns:
            Valid access token if available, None otherwise.
        """
        normalized_phone = self._normalize_phone(phone_number)
        doc_id = f"{normalized_phone}_{feature}"
        doc_ref = self._tokens_collection.document(doc_id)
        doc = await doc_ref.get()

        if not doc.exists:
            logger.debug(f"No OAuth token found for phone {phone_number[:4]}****, feature: {feature}")
            return None

        token = OAuthToken.from_firestore(doc.to_dict())

        # Check if token is still valid
        if not token.is_expired():
            return token.access_token

        # Token is expired, try to refresh
        logger.info(f"Access token expired for {phone_number[:4]}**** ({feature}), attempting refresh")
        refreshed_token = await self._refresh_access_token(phone_number, token.email, feature)

        if refreshed_token:
            return refreshed_token

        logger.warning(f"Failed to refresh token for {phone_number[:4]}**** ({feature})")
        return None

    async def _refresh_access_token(
        self, phone_number: str, email: str, feature: str = "calendar"
    ) -> Optional[str]:
        """Refresh an expired access token using the refresh token.

        Args:
            phone_number: User's phone number in E.164 format.
            email: Google account email (for updating token record).
            feature: Feature to refresh token for.

        Returns:
            New access token if successful, None otherwise.
        """
        # Get refresh token from Secret Manager
        secret_key = f"{phone_number}-{feature}"
        refresh_token = await self._secret_manager.get_refresh_token(secret_key)
        
        if not refresh_token:
            logger.error(f"No refresh token found for {phone_number[:4]}**** ({feature})")
            return None

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "refresh_token": refresh_token,
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "grant_type": "refresh_token",
                    },
                )
                response.raise_for_status()
                token_data = response.json()

                access_token = token_data.get("access_token")
                expires_in = token_data.get("expires_in", 3600)
                # Note: Refresh response may include new refresh_token
                new_refresh_token = token_data.get("refresh_token")

                if access_token:
                    await self.store_tokens(
                        phone_number, email, access_token, new_refresh_token, expires_in, feature
                    )
                    logger.info(f"Refreshed access token for {phone_number[:4]}**** ({feature})")
                    return access_token

                return None

            except httpx.HTTPStatusError as e:
                logger.error(f"Token refresh failed: {e.response.status_code}")
                # If refresh fails (e.g., token revoked), clean up
                if e.response.status_code in (400, 401):
                    await self.revoke_tokens(phone_number, feature)
                return None
            except Exception as e:
                logger.error(f"Token refresh error: {e}")
                return None

    async def get_token_info(self, phone_number: str, feature: str = "calendar") -> Optional[OAuthToken]:
        """Get token information for a phone number.

        Args:
            phone_number: User's phone number in E.164 format.
            feature: Feature to get info for.

        Returns:
            OAuthToken if found, None otherwise.
        """
        normalized_phone = self._normalize_phone(phone_number)
        doc_id = f"{normalized_phone}_{feature}"
        doc_ref = self._tokens_collection.document(doc_id)
        doc = await doc_ref.get()

        if not doc.exists:
            return None

        return OAuthToken.from_firestore(doc.to_dict())

    async def has_valid_token(self, phone_number: str, feature: str = "calendar") -> bool:
        """Check if a phone number has a valid (or refreshable) OAuth token.

        Args:
            phone_number: User's phone number in E.164 format.
            feature: Feature to check.

        Returns:
            True if valid token exists or can be refreshed, False otherwise.
        """
        token = await self.get_access_token(phone_number, feature)
        return token is not None

    async def revoke_tokens(self, phone_number: str, feature: str = "calendar") -> bool:
        """Revoke and delete all tokens for a phone number and feature.

        Args:
            phone_number: User's phone number in E.164 format.
            feature: Feature to revoke.

        Returns:
            True if successful, False otherwise.
        """
        normalized_phone = self._normalize_phone(phone_number)
        doc_id = f"{normalized_phone}_{feature}"

        try:
            # Delete from Firestore
            doc_ref = self._tokens_collection.document(doc_id)
            await doc_ref.delete()

            # Delete from Secret Manager
            secret_key = f"{phone_number}-{feature}"
            await self._secret_manager.delete_refresh_token(secret_key)
            
            logger.info(f"Revoked tokens for phone {phone_number[:4]}****, feature: {feature}")
            return True

        except Exception as e:
            logger.error(f"Failed to revoke tokens: {e}")
            return False
