"""Unit tests for Discord gateway attachment handling."""
import pytest

from src.discord.gateway import (
    FALLBACK_MIME_TYPE,
    MAX_INLINE_ATTACHMENT_BYTES,
    _build_discord_message_content,
)


class _FakeAttachment:
    def __init__(self, filename: str, content_type: str | None, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self.size = len(data)

    async def read(self) -> bytes:
        return self._data


@pytest.mark.asyncio
async def test_build_content_text_only():
    content = await _build_discord_message_content("hello", [])

    assert content.role == "user"
    assert len(content.parts) == 1
    assert content.parts[0].text == "hello"


@pytest.mark.asyncio
async def test_build_content_with_attachment():
    attachment = _FakeAttachment(
        filename="resume.pdf",
        content_type="application/pdf",
        data=b"%PDF-1.4",
    )

    content = await _build_discord_message_content("Please review this", [attachment])

    assert content.role == "user"
    assert len(content.parts) == 2
    assert "Please review this" in content.parts[0].text
    assert "resume.pdf" in content.parts[0].text
    assert content.parts[1].inline_data.mime_type == "application/pdf"
    assert content.parts[1].inline_data.data == b"%PDF-1.4"


@pytest.mark.asyncio
async def test_build_content_attachment_only():
    attachment = _FakeAttachment(
        filename="photo.png",
        content_type="image/png",
        data=b"\x89PNG",
    )

    content = await _build_discord_message_content("", [attachment])

    assert len(content.parts) == 2
    assert "no accompanying text" in content.parts[0].text
    assert "photo.png" in content.parts[0].text
    assert content.parts[1].inline_data.mime_type == "image/png"


@pytest.mark.asyncio
async def test_build_content_uses_fallback_mime_type():
    attachment = _FakeAttachment(
        filename="blob.bin",
        content_type=None,
        data=b"\x00\x01",
    )

    content = await _build_discord_message_content("binary", [attachment])

    assert content.parts[1].inline_data.mime_type == FALLBACK_MIME_TYPE


@pytest.mark.asyncio
async def test_build_content_rejects_oversized_attachment():
    attachment = _FakeAttachment(
        filename="large.pdf",
        content_type="application/pdf",
        data=b"x" * (MAX_INLINE_ATTACHMENT_BYTES + 1),
    )

    with pytest.raises(ValueError, match="too large"):
        await _build_discord_message_content("review", [attachment])
