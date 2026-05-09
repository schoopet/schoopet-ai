"""Message channel definitions."""
from enum import Enum


class MessageChannel(str, Enum):
    """Messaging channel for incoming/outgoing messages."""

    TELEGRAM = "telegram"
    SLACK = "slack"
    EMAIL = "email"
    DISCORD = "discord"
