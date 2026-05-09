"""Unit tests for MessageChannel enum."""
import pytest

from src.channel import MessageChannel


class TestMessageChannel:
    """Tests for MessageChannel enum."""

    def test_channel_is_string_enum(self):
        """MessageChannel values should be usable as strings."""
        assert MessageChannel.DISCORD.value == "discord"
        assert MessageChannel.TELEGRAM.value == "telegram"
        assert MessageChannel.SLACK.value == "slack"
        assert MessageChannel.EMAIL.value == "email"

    def test_channel_values_are_strings(self):
        """Channel instances should compare equal to their string values."""
        assert MessageChannel.DISCORD == "discord"
        assert MessageChannel.TELEGRAM == "telegram"
