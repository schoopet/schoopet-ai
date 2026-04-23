"""Unit tests for Discord Ed25519 signature validation."""
import binascii

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption

from src.discord.validator import DiscordValidator


def _generate_keypair():
    """Generate a test Ed25519 keypair. Returns (private_key_hex, public_key_hex)."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_hex = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()
    public_hex = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return private_hex, public_hex


def _sign(private_key_hex: str, timestamp: str, body: bytes) -> str:
    """Sign a Discord interaction message with the given private key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(binascii.unhexlify(private_key_hex))
    message = timestamp.encode() + body
    return key.sign(message).hex()


class TestDiscordValidator:
    @pytest.fixture
    def keypair(self):
        return _generate_keypair()

    @pytest.fixture
    def validator(self, keypair):
        _, public_key_hex = keypair
        return DiscordValidator(public_key_hex)

    def test_valid_signature_returns_true(self, keypair, validator):
        private_hex, _ = keypair
        body = b'{"type":2,"data":{"name":"chat"}}'
        timestamp = "1234567890"
        signature = _sign(private_hex, timestamp, body)
        assert validator.validate(signature, timestamp, body) is True

    def test_missing_signature_returns_false(self, validator):
        assert validator.validate("", "1234567890", b"body") is False

    def test_missing_timestamp_returns_false(self, validator):
        assert validator.validate("aabbcc", "", b"body") is False

    def test_wrong_signature_returns_false(self, keypair, validator):
        private_hex, _ = keypair
        body = b'{"type":2}'
        timestamp = "1234567890"
        # Sign different body
        bad_sig = _sign(private_hex, timestamp, b"other body")
        assert validator.validate(bad_sig, timestamp, body) is False

    def test_tampered_body_returns_false(self, keypair, validator):
        private_hex, _ = keypair
        body = b'{"type":2,"data":{"name":"chat"}}'
        timestamp = "1234567890"
        signature = _sign(private_hex, timestamp, body)
        assert validator.validate(signature, timestamp, b'{"type":2,"data":{"name":"evil"}}') is False

    def test_wrong_key_returns_false(self):
        _, public_hex_1 = _generate_keypair()
        private_hex_2, _ = _generate_keypair()
        validator = DiscordValidator(public_hex_1)
        body = b'{"type":1}'
        timestamp = "9999999999"
        signature = _sign(private_hex_2, timestamp, body)
        assert validator.validate(signature, timestamp, body) is False

    def test_invalid_hex_signature_returns_false(self, validator):
        assert validator.validate("not-hex!!", "1234567890", b"body") is False

    def test_ping_body_valid(self, keypair, validator):
        private_hex, _ = keypair
        body = b'{"type":1}'
        timestamp = "1000000000"
        signature = _sign(private_hex, timestamp, body)
        assert validator.validate(signature, timestamp, body) is True
