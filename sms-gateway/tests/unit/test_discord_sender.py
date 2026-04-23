"""Unit tests for Discord message splitting."""
from src.discord.sender import _split_message, MAX_MESSAGE_LENGTH


class TestSplitMessage:
    def test_short_message_not_split(self):
        assert _split_message("hello") == ["hello"]

    def test_exact_limit_not_split(self):
        text = "x" * MAX_MESSAGE_LENGTH
        assert _split_message(text) == [text]

    def test_over_limit_splits(self):
        text = "x" * (MAX_MESSAGE_LENGTH + 1)
        chunks = _split_message(text)
        assert len(chunks) == 2
        assert all(len(c) <= MAX_MESSAGE_LENGTH for c in chunks)
        assert "".join(chunks) == text

    def test_splits_at_newline(self):
        line = "a" * (MAX_MESSAGE_LENGTH - 10)
        text = line + "\n" + "b" * 100
        chunks = _split_message(text)
        assert chunks[0] == line
        assert chunks[1] == "b" * 100

    def test_splits_at_space(self):
        word = "a" * (MAX_MESSAGE_LENGTH - 5)
        text = word + " extra"
        chunks = _split_message(text)
        assert chunks[0] == word
        assert chunks[1] == "extra"

    def test_hard_cut_when_no_whitespace(self):
        text = "x" * (MAX_MESSAGE_LENGTH * 2)
        chunks = _split_message(text)
        assert all(len(c) <= MAX_MESSAGE_LENGTH for c in chunks)
        assert "".join(chunks) == text

    def test_empty_string(self):
        assert _split_message("") == [""]

    def test_multipart_all_within_limit(self):
        text = ("word " * 500).strip()  # ~2500 chars
        chunks = _split_message(text)
        assert all(len(c) <= MAX_MESSAGE_LENGTH for c in chunks)
        # All content is preserved across chunks (leading/trailing spaces stripped at boundaries)
        rejoined = " ".join(chunks)
        assert rejoined == text
