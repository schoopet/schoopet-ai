"""Shared channel-routing helpers for Discord responses.

Parses <CHANNEL:id>...</CHANNEL> tags that the agent emits to route parts of
a response to specific Discord channels instead of the default reply target.
"""
import re

CHANNEL_TAG_RE = re.compile(r"<CHANNEL:(\d+)>(.*?)</CHANNEL>", re.DOTALL)


def parse_channel_tags(text: str) -> tuple[list[tuple[str, str]], str]:
    """Split agent text into routed blocks and a remainder.

    Returns:
        routed: list of (channel_id, content) for each <CHANNEL:id> block found.
        remainder: text with all <CHANNEL> tags stripped out.
    """
    routed = [(cid, content.strip()) for cid, content in CHANNEL_TAG_RE.findall(text)]
    remainder = CHANNEL_TAG_RE.sub("", text).strip()
    return routed, remainder
