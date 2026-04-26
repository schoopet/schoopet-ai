"""Shared OAuth client for agent tools."""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import logging

from .utils import normalize_user_id

# Firestore collection for OAuth tokens (shared with SMS gateway)
TOKENS_COLLECTION = "oauth_tokens"
TOKEN_URL = "https://oauth2.googleapis.com/token"

class OAuthClient:
    """Handles OAuth token retrieval, refresh, and authorization link generation."""

    def __init__(self):
        """Initialize the OAuth Client - all initialization is deferred."""
        self._firestore_client = None
        self._secret_client = None
        self._initialized = False

        self._project_id = None
        self._oauth_base_url = None
        self._client_id = None
        self._client_secret = None
        self._hmac_secret = None

    def _ensure_initialized(self):
        """Lazy initialization of configuration."""
        if self._initialized:
            return

        self._project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self._oauth_base_url = os.getenv("OAUTH_BASE_URL", "")
        
        # Try environment first
        self._client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        self._client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
        
        self._initialized = True
        
        # Fallback to Secret Manager if missing
        if not self._client_id:
            self._client_id = self._get_secret("google-oauth-client-id") or ""
        if not self._client_secret:
            self._client_secret = self._get_secret("google-oauth-client-secret") or ""

    def _get_firestore_client(self):
        """Get Firestore client, initializing lazily."""
        if self._firestore_client is None:
            self._ensure_initialized()
            if self._project_id:
                # Import here to avoid issues during pickling
                from google.cloud import firestore
                self._firestore_client = firestore.Client(project=self._project_id)
        return self._firestore_client

    def _get_secret_client(self):
        """Get Secret Manager client, initializing lazily."""
        if self._secret_client is None:
            self._ensure_initialized()
            if self._project_id:
                # Import here to avoid issues during pickling
                from google.cloud import secretmanager
                self._secret_client = secretmanager.SecretManagerServiceClient()
        return self._secret_client

    def _get_secret(self, secret_id: str) -> Optional[str]:
        """Get a secret value from Secret Manager."""
        secret_client = self._get_secret_client()
        if not secret_client or not self._project_id:
            return None

        try:
            name = f"projects/{self._project_id}/secrets/{secret_id}/versions/latest"
            response = secret_client.access_secret_version(request={"name": name})
            return response.payload.data.decode("UTF-8")
        except Exception as e:
            print(f"Error getting secret {secret_id}: {e}")
            return None

    def _get_hmac_secret(self) -> Optional[str]:
        """Get HMAC secret from Secret Manager (cached)."""
        if self._hmac_secret is not None:
            return self._hmac_secret

        self._hmac_secret = self._get_secret("oauth-hmac-secret")
        return self._hmac_secret

    def get_tool_token(
        self,
        user_id: str,
    ):
        """Return (token, error_or_link) for a personal tool.

        Uses user_id's google token; returns an OAuth link on failure.
        """
        token = self.get_valid_access_token(user_id, "google")
        return token, (None if token else self.get_oauth_link(user_id))

    def get_oauth_link(self, user_id: str, feature: str = "google") -> str:
        """Generate secure HMAC-signed OAuth authorization link."""
        self._ensure_initialized()

        if not self._oauth_base_url:
            return f"Authorization not configured ({feature}). Please contact support."

        secret = self._get_hmac_secret()
        if not secret:
            return f"Authorization temporarily unavailable ({feature}). Please try again later."

        import hmac as hmac_lib
        import hashlib
        import base64
        import time

        expires = int(time.time()) + 600  # 10 minutes
        message = f"{user_id}:{expires}"

        signature = hmac_lib.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        token = base64.urlsafe_b64encode(f"{message}:{signature}".encode()).decode()

        return f"{self._oauth_base_url}/oauth/google/initiate?token={token}&feature={feature}"

    def get_token_data(self, user_id: str, feature: str) -> Optional[Dict[str, Any]]:
        """Get OAuth token data from Firestore."""
        firestore_client = self._get_firestore_client()
        if not firestore_client:
            return None

        normalized = normalize_user_id(user_id)
        doc_id = f"{normalized}_{feature}"
        doc_ref = firestore_client.collection(TOKENS_COLLECTION).document(doc_id)
        doc = doc_ref.get()

        if doc.exists:
            return doc.to_dict()

        return None

    def is_token_expired(self, token_data: Dict[str, Any]) -> bool:
        """Check if access token is expired."""
        expires_at = token_data.get("expires_at")
        if not expires_at:
            return True

        now = datetime.now(timezone.utc)
        if hasattr(expires_at, 'tzinfo') and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        return now >= (expires_at - timedelta(seconds=60))

    def _get_refresh_token(self, user_id: str, feature: str) -> Optional[str]:
        """Get refresh token from Secret Manager."""
        self._ensure_initialized()
        secret_client = self._get_secret_client()
        if not secret_client:
            return None

        try:
            normalized = normalize_user_id(user_id)
            secret_key = f"{normalized}-{feature}"
            secret_name = f"projects/{self._project_id}/secrets/oauth-refresh-{secret_key}/versions/latest"
            response = secret_client.access_secret_version(request={"name": secret_name})
            return response.payload.data.decode("UTF-8")
        except Exception as e:
            if "NotFound" in str(type(e).__name__):
                return None
            print(f"Error getting refresh token: {e}")
            return None

    def _refresh_access_token(self, user_id: str, refresh_token: str, feature: str) -> Optional[str]:
        """Refresh an expired access token."""
        self._ensure_initialized()
        if not self._client_id or not self._client_secret:
            return None

        try:
            import httpx

            with httpx.Client() as client:
                response = client.post(
                    TOKEN_URL,
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

                if access_token:
                    # Update token in Firestore
                    firestore_client = self._get_firestore_client()
                    if firestore_client:
                        normalized = normalize_user_id(user_id)
                        doc_id = f"{normalized}_{feature}"
                        doc_ref = firestore_client.collection(TOKENS_COLLECTION).document(doc_id)
                        now = datetime.now(timezone.utc)
                        doc_ref.set({
                            "user_id": user_id,
                            "feature": feature,
                            "access_token": access_token,
                            "expires_at": now + timedelta(seconds=expires_in),
                            "updated_at": now,
                            "email": "unknown", # Might lose email on refresh if not careful
                            "token_type": "Bearer"
                        }, merge=True)
                    return access_token

                return None
        except Exception as e:
            print(f"Error refreshing token: {e}")
            return None

    def get_valid_access_token(self, user_id: str, feature: str) -> Optional[str]:
        """Get a valid access token, refreshing if necessary."""
        token_data = self.get_token_data(user_id, feature)
        if not token_data:
            return None

        access_token = token_data.get("access_token")

        if self.is_token_expired(token_data):
            refresh_token = self._get_refresh_token(user_id, feature)
            if refresh_token:
                access_token = self._refresh_access_token(user_id, refresh_token, feature)

        return access_token

