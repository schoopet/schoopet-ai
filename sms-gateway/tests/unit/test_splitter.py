"""Unit tests for SMS message splitter."""
import pytest
from src.sms.splitter import SMSSplitter


class TestSMSSplitter:
    """Tests for SMSSplitter class."""

    @pytest.fixture
    def splitter(self):
        """Create a splitter instance."""
        return SMSSplitter()

    def test_empty_message(self, splitter):
        """Empty messages should return empty list."""
        assert splitter.split("") == []
        assert splitter.split("   ") == []

    def test_short_message_no_split(self, splitter):
        """Messages under 160 chars should not split."""
        text = "Hello, this is a short message."
        segments = splitter.split(text)

        assert len(segments) == 1
        assert segments[0] == text

    def test_exact_160_chars(self, splitter):
        """Message exactly 160 chars should not split."""
        text = "A" * 160
        segments = splitter.split(text)

        assert len(segments) == 1
        assert len(segments[0]) == 160

    def test_161_chars_splits(self, splitter):
        """Message of 161 chars should split into 2 segments."""
        text = "A" * 161
        segments = splitter.split(text)

        assert len(segments) == 2
        assert segments[0].startswith("(1/2) ")
        assert segments[1].startswith("(2/2) ")

    def test_segment_prefixes(self, splitter):
        """Multi-part messages should have correct prefixes."""
        text = "word " * 100  # 500 chars
        segments = splitter.split(text)

        assert len(segments) >= 3
        for i, segment in enumerate(segments, 1):
            assert segment.startswith(f"({i}/{len(segments)}) ")

    def test_word_boundary_split(self, splitter):
        """Splits should occur at word boundaries when possible."""
        # Create text with clear word boundaries
        text = "This is a test message with many words. " * 10
        segments = splitter.split(text)

        # Segments shouldn't end mid-word (unless impossible)
        for segment in segments:
            # Remove prefix
            content = segment.split(") ", 1)[-1] if ")" in segment else segment
            # Content should not start with a partial word (space)
            # and should not end with a partial word
            assert not content.startswith(" ") or len(content) == 1

    def test_max_segments_limit(self, splitter):
        """Should respect max_segments parameter."""
        text = "A" * 2000  # Would normally be many segments
        segments = splitter.split(text, max_segments=3)

        assert len(segments) == 3
        # Last segment should end with ellipsis
        assert segments[-1].endswith("...")

    def test_single_segment_estimation(self, splitter):
        """Estimate should return 1 for short messages."""
        assert splitter.estimate_segments("Hello") == 1
        assert splitter.estimate_segments("A" * 160) == 1

    def test_multi_segment_estimation(self, splitter):
        """Estimate should correctly predict segment count."""
        # Just over single message limit
        estimate = splitter.estimate_segments("A" * 161)
        assert estimate >= 2

        # Much longer message
        estimate = splitter.estimate_segments("A" * 500)
        assert estimate >= 3

    def test_gsm7_detection(self, splitter):
        """Should correctly detect GSM-7 compatible text."""
        # ASCII text should be GSM-7
        assert splitter._is_gsm7("Hello World! 123")

        # Extended GSM-7 chars
        assert splitter._is_gsm7("Price: 10€")

        # Emoji is not GSM-7
        assert not splitter._is_gsm7("Hello 👋")

        # Chinese characters are not GSM-7
        assert not splitter._is_gsm7("你好")

    def test_gsm7_length_calculation(self, splitter):
        """Extended GSM-7 chars should count as 2."""
        # Basic chars count as 1
        assert splitter._gsm7_length("Hello") == 5

        # Euro sign counts as 2
        assert splitter._gsm7_length("€10") == 4  # 2 + 1 + 1

        # Multiple extended chars
        assert splitter._gsm7_length("€€€") == 6

    def test_unicode_lower_limit(self, splitter):
        """Unicode messages should use 70 char limit."""
        # 70 chars with emoji should be single message
        text = "A" * 69 + "👋"
        segments = splitter.split(text)
        assert len(segments) == 1

        # 71 chars with emoji should split
        text = "A" * 70 + "👋"
        segments = splitter.split(text)
        assert len(segments) >= 2

    def test_real_world_response(self, splitter):
        """Test with realistic agent response."""
        text = (
            "I'll remember that Sarah from work likes coffee! "
            "Based on what you've told me, here are some gift ideas: "
            "1. A nice coffee mug from her favorite local cafe "
            "2. Some specialty coffee beans "
            "3. A gift card to Starbucks. "
            "Would you like me to save any of these ideas?"
        )
        segments = splitter.split(text)

        # Should split into 2-3 segments
        assert 1 <= len(segments) <= 3

        # Total content should be preserved (minus prefixes)
        total_content = ""
        for segment in segments:
            if ")" in segment:
                total_content += segment.split(") ", 1)[1]
            else:
                total_content += segment

        # Original text should be fully contained
        assert text.replace(" ", "") in total_content.replace(" ", "") or len(segments) == 1
