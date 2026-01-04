"""SMS message splitting logic."""
import re
from typing import List

# GSM-7 character set (standard SMS encoding)
GSM7_BASIC = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ ÆæßÉ"
    " !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)

# Characters that count as 2 in GSM-7 (escape characters)
GSM7_EXTENDED = "^{}\\[~]|€"


class SMSSplitter:
    """Splits long messages into SMS-appropriate segments.

    SMS messages have different character limits based on encoding:
    - GSM-7 (standard ASCII + some symbols): 160 chars single, 153 chars per segment
    - UCS-2 (Unicode/emoji): 70 chars single, 67 chars per segment

    Multi-part messages reserve space for segment indicators like "(1/3) ".
    """

    GSM7_SINGLE_LIMIT = 160
    GSM7_MULTI_LIMIT = 153  # Reserve 7 chars for segment prefix "(1/9) "
    UCS2_SINGLE_LIMIT = 70
    UCS2_MULTI_LIMIT = 67

    # Segment prefix format: "(1/3) " = 6 chars, "(1/10) " = 7 chars
    SEGMENT_PREFIX_LEN = 7

    def __init__(self):
        """Initialize the SMS splitter."""
        self._gsm7_chars = set(GSM7_BASIC + GSM7_EXTENDED)

    def _is_gsm7(self, text: str) -> bool:
        """Check if text can be encoded using GSM-7.

        Args:
            text: The text to check.

        Returns:
            True if all characters are in GSM-7 set.
        """
        return all(char in self._gsm7_chars for char in text)

    def _gsm7_length(self, text: str) -> int:
        """Calculate GSM-7 encoded length (extended chars count as 2).

        Args:
            text: The text to measure.

        Returns:
            The GSM-7 character count.
        """
        length = 0
        for char in text:
            if char in GSM7_EXTENDED:
                length += 2
            else:
                length += 1
        return length

    def _split_at_boundary(self, text: str, limit: int) -> tuple[str, str]:
        """Split text at word boundary, respecting character limit.

        Attempts to split at whitespace to avoid breaking words.
        Falls back to hard split if no good boundary found.

        Args:
            text: Text to split.
            limit: Maximum characters for first segment.

        Returns:
            Tuple of (segment, remainder).
        """
        if len(text) <= limit:
            return text, ""

        # Look for last whitespace within limit
        segment = text[:limit]
        last_space = segment.rfind(" ")

        if last_space > limit // 2:  # Found space in reasonable position
            return text[:last_space].rstrip(), text[last_space:].lstrip()
        else:
            # No good boundary, hard split
            return text[:limit], text[limit:]

    def split(self, text: str, max_segments: int = 10) -> List[str]:
        """Split text into SMS segments.

        For single-message texts, returns the text as-is.
        For multi-part texts, adds segment prefixes like "(1/3) ".

        Args:
            text: The full response text.
            max_segments: Maximum number of SMS to send (prevents runaway costs).

        Returns:
            List of message segments ready for sending.
        """
        text = text.strip()

        if not text:
            return []

        # Determine encoding and limits
        is_gsm7 = self._is_gsm7(text)

        if is_gsm7:
            single_limit = self.GSM7_SINGLE_LIMIT
            multi_limit = self.GSM7_MULTI_LIMIT
            text_length = self._gsm7_length(text)
        else:
            single_limit = self.UCS2_SINGLE_LIMIT
            multi_limit = self.UCS2_MULTI_LIMIT
            text_length = len(text)

        # Check if single message is sufficient
        if text_length <= single_limit:
            return [text]

        # Need to split into multiple segments
        segments = []
        remaining = text

        # First pass: split text into segments
        while remaining and len(segments) < max_segments:
            # Account for prefix length in available space
            available = multi_limit - self.SEGMENT_PREFIX_LEN
            segment, remaining = self._split_at_boundary(remaining, available)
            segments.append(segment)

        # Handle overflow (text exceeds max_segments)
        if remaining:
            # Truncate last segment and add ellipsis
            last_segment = segments[-1]
            if len(last_segment) > available - 3:
                last_segment = last_segment[: available - 3]
            segments[-1] = last_segment.rstrip() + "..."

        # Add segment prefixes
        total = len(segments)
        prefixed_segments = []
        for i, segment in enumerate(segments, 1):
            prefix = f"({i}/{total}) "
            prefixed_segments.append(prefix + segment)

        return prefixed_segments

    def estimate_segments(self, text: str) -> int:
        """Estimate how many SMS segments a text will require.

        Args:
            text: The text to estimate.

        Returns:
            Estimated number of SMS segments.
        """
        text = text.strip()
        if not text:
            return 0

        is_gsm7 = self._is_gsm7(text)

        if is_gsm7:
            single_limit = self.GSM7_SINGLE_LIMIT
            multi_limit = self.GSM7_MULTI_LIMIT
            text_length = self._gsm7_length(text)
        else:
            single_limit = self.UCS2_SINGLE_LIMIT
            multi_limit = self.UCS2_MULTI_LIMIT
            text_length = len(text)

        if text_length <= single_limit:
            return 1

        # Calculate segments needed
        available_per_segment = multi_limit - self.SEGMENT_PREFIX_LEN
        return (text_length + available_per_segment - 1) // available_per_segment
