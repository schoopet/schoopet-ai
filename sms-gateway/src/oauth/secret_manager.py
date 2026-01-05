"""Secret Manager client for OAuth refresh token storage."""
import logging
from typing import Optional

from google.cloud import secretmanager
from google.api_core import exceptions

logger = logging.getLogger(__name__)

# Secret name prefix for OAuth refresh tokens
SECRET_PREFIX = "oauth-refresh"


class SecretManagerClient:
    """Manages OAuth refresh tokens in Google Cloud Secret Manager.

    Refresh tokens are stored as secrets with naming convention:
    oauth-refresh-{normalized_phone_number}

    This provides encryption at rest and audit logging.
    """

    def __init__(self, project_id: str):
        """Initialize Secret Manager client.

        Args:
            project_id: Google Cloud project ID.
        """
        self._client = secretmanager.SecretManagerServiceClient()
        self._project_id = project_id

    def _normalize_phone(self, phone_number: str) -> str:
        """Normalize phone number for secret naming.

        Secret names must match: [a-zA-Z0-9_-]+
        """
        return phone_number.lstrip("+").replace("-", "").replace(" ", "")

    def _get_secret_id(self, phone_number: str) -> str:
        """Get secret ID for a phone number."""
        return f"{SECRET_PREFIX}-{self._normalize_phone(phone_number)}"

    def _get_secret_name(self, phone_number: str) -> str:
        """Get full secret resource name."""
        secret_id = self._get_secret_id(phone_number)
        return f"projects/{self._project_id}/secrets/{secret_id}"

    def _get_secret_version_name(self, phone_number: str) -> str:
        """Get latest secret version resource name."""
        return f"{self._get_secret_name(phone_number)}/versions/latest"

    async def store_refresh_token(self, phone_number: str, refresh_token: str) -> bool:
        """Store a refresh token in Secret Manager.

        Creates a new secret if it doesn't exist, or adds a new version
        if it does. Old versions are automatically disabled.

        Args:
            phone_number: User's phone number in E.164 format.
            refresh_token: OAuth refresh token to store.

        Returns:
            True if successful, False otherwise.
        """
        secret_id = self._get_secret_id(phone_number)
        secret_name = self._get_secret_name(phone_number)

        try:
            # Try to create the secret first
            try:
                self._client.create_secret(
                    request={
                        "parent": f"projects/{self._project_id}",
                        "secret_id": secret_id,
                        "secret": {
                            "replication": {"automatic": {}},
                            "labels": {"type": "oauth-refresh-token"},
                        },
                    }
                )
                logger.info(f"Created new secret for phone {phone_number[:4]}****")
            except exceptions.AlreadyExists:
                # Secret already exists, we'll add a new version
                pass

            # Add new secret version with the refresh token
            self._client.add_secret_version(
                request={
                    "parent": secret_name,
                    "payload": {"data": refresh_token.encode("UTF-8")},
                }
            )
            logger.info(f"Stored refresh token for phone {phone_number[:4]}****")
            return True

        except Exception as e:
            logger.error(f"Failed to store refresh token: {e}")
            return False

    async def get_refresh_token(self, phone_number: str) -> Optional[str]:
        """Retrieve a refresh token from Secret Manager.

        Args:
            phone_number: User's phone number in E.164 format.

        Returns:
            Refresh token if found, None otherwise.
        """
        try:
            version_name = self._get_secret_version_name(phone_number)
            response = self._client.access_secret_version(
                request={"name": version_name}
            )
            return response.payload.data.decode("UTF-8")

        except exceptions.NotFound:
            logger.debug(f"No refresh token found for phone {phone_number[:4]}****")
            return None
        except Exception as e:
            logger.error(f"Failed to retrieve refresh token: {e}")
            return None

    async def delete_refresh_token(self, phone_number: str) -> bool:
        """Delete a refresh token from Secret Manager.

        This deletes the entire secret (all versions).

        Args:
            phone_number: User's phone number in E.164 format.

        Returns:
            True if successful or not found, False on error.
        """
        try:
            secret_name = self._get_secret_name(phone_number)
            self._client.delete_secret(request={"name": secret_name})
            logger.info(f"Deleted refresh token for phone {phone_number[:4]}****")
            return True

        except exceptions.NotFound:
            # Already deleted, that's fine
            return True
        except Exception as e:
            logger.error(f"Failed to delete refresh token: {e}")
            return False

    async def has_refresh_token(self, phone_number: str) -> bool:
        """Check if a refresh token exists for a phone number.

        Args:
            phone_number: User's phone number in E.164 format.

        Returns:
            True if token exists, False otherwise.
        """
        try:
            version_name = self._get_secret_version_name(phone_number)
            self._client.access_secret_version(request={"name": version_name})
            return True
        except exceptions.NotFound:
            return False
        except Exception as e:
            logger.error(f"Failed to check refresh token: {e}")
            return False
