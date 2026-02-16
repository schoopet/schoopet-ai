"""Telegram webhook secret token validation."""
import hashlib
import logging

logger = logging.getLogger(__name__)


class TelegramValidator:
    """Validates Telegram webhook requests using a secret token.

    The secret is derived deterministically from the bot token:
    SHA256(bot_token), first 32 characters. This must match the
    secret_token set when registering the webhook with Telegram.
    """

    def __init__(self, bot_token: str):
        """Initialize validator with bot token.

        Args:
            bot_token: Telegram Bot API token from @BotFather.
        """
        self._secret = hashlib.sha256(bot_token.encode()).hexdigest()[:32]

    def validate(self, secret_header: str) -> bool:
        """Validate the X-Telegram-Bot-Api-Secret-Token header.

        Args:
            secret_header: Value of the X-Telegram-Bot-Api-Secret-Token header.

        Returns:
            True if the secret matches, False otherwise.
        """
        if not secret_header:
            logger.warning("Missing X-Telegram-Bot-Api-Secret-Token header")
            return False

        is_valid = secret_header == self._secret
        if not is_valid:
            logger.warning("Invalid Telegram webhook secret token")

        return is_valid
