"""Unit tests for Slack request signature validation."""
import hashlib
import hmac
import time
import pytest

from src.slack.validator import SlackValidator, MAX_REQUEST_AGE_SECONDS


SIGNING_SECRET = "test_signing_secret_abc123"


def _make_signature(secret: str, timestamp: str, body: bytes) -> str:
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode()
    digest = hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return f"v0={digest}"


class TestSlackValidator:
    @pytest.fixture
    def validator(self):
        return SlackValidator(SIGNING_SECRET)

    def test_valid_signature_returns_true(self, validator):
        timestamp = str(int(time.time()))
        body = b'{"type":"event_callback"}'
        signature = _make_signature(SIGNING_SECRET, timestamp, body)
        assert validator.validate(timestamp, body, signature) is True

    def test_missing_timestamp_returns_false(self, validator):
        body = b'{"type":"event_callback"}'
        signature = _make_signature(SIGNING_SECRET, str(int(time.time())), body)
        assert validator.validate("", body, signature) is False

    def test_missing_signature_returns_false(self, validator):
        timestamp = str(int(time.time()))
        body = b'{"type":"event_callback"}'
        assert validator.validate(timestamp, body, "") is False

    def test_invalid_signature_returns_false(self, validator):
        timestamp = str(int(time.time()))
        body = b'{"type":"event_callback"}'
        assert validator.validate(timestamp, body, "v0=badhash") is False

    def test_stale_timestamp_returns_false(self, validator):
        old_timestamp = str(int(time.time()) - MAX_REQUEST_AGE_SECONDS - 1)
        body = b'{"type":"event_callback"}'
        signature = _make_signature(SIGNING_SECRET, old_timestamp, body)
        assert validator.validate(old_timestamp, body, signature) is False

    def test_non_numeric_timestamp_returns_false(self, validator):
        body = b'{"type":"event_callback"}'
        assert validator.validate("not-a-number", body, "v0=anything") is False

    def test_wrong_secret_returns_false(self, validator):
        timestamp = str(int(time.time()))
        body = b'{"type":"event_callback"}'
        signature = _make_signature("wrong_secret", timestamp, body)
        assert validator.validate(timestamp, body, signature) is False

    def test_future_timestamp_within_limit_returns_true(self, validator):
        # Slightly in the future (clock skew) should still be valid
        timestamp = str(int(time.time()) + 60)
        body = b'{"type":"event_callback"}'
        signature = _make_signature(SIGNING_SECRET, timestamp, body)
        assert validator.validate(timestamp, body, signature) is True

    def test_empty_body_valid_signature(self, validator):
        timestamp = str(int(time.time()))
        body = b""
        signature = _make_signature(SIGNING_SECRET, timestamp, body)
        assert validator.validate(timestamp, body, signature) is True
