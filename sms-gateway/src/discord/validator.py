"""Discord interaction request signature validation using Ed25519."""
import binascii
import logging

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger(__name__)


class DiscordValidator:
    """Validates Discord interaction requests using Ed25519 signatures.

    Discord signs every interaction with the app's public key. The message
    being signed is: timestamp_bytes + body_bytes.
    """

    def __init__(self, public_key: str):
        """Initialize validator with the Discord application public key.

        Args:
            public_key: Hex-encoded Ed25519 public key from the Discord
                        Developer Portal (Application → General Information).
        """
        key_bytes = binascii.unhexlify(public_key)
        self._public_key = Ed25519PublicKey.from_public_bytes(key_bytes)

    def validate(self, signature: str, timestamp: str, body: bytes) -> bool:
        """Validate the Ed25519 signature of a Discord interaction request.

        Args:
            signature: Value of the X-Signature-Ed25519 header (hex-encoded).
            timestamp: Value of the X-Signature-Timestamp header.
            body: Raw request body bytes.

        Returns:
            True if the signature is valid, False otherwise.
        """
        if not signature or not timestamp:
            logger.warning("Missing Discord signature or timestamp header")
            return False

        try:
            sig_bytes = binascii.unhexlify(signature)
            message = timestamp.encode() + body
            self._public_key.verify(sig_bytes, message)
            return True
        except (InvalidSignature, binascii.Error, ValueError):
            logger.warning("Invalid Discord request signature")
            return False
