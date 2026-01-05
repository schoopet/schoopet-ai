"""Unit tests for rate limiter."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from src.ratelimit.limiter import RateLimiter


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_normalize_phone(self):
        """Test phone number normalization."""
        mock_client = MagicMock()
        limiter = RateLimiter(mock_client, daily_limit=100)

        assert limiter._normalize_phone("+19494136310") == "19494136310"
        assert limiter._normalize_phone("1-949-413-6310") == "19494136310"
        assert limiter._normalize_phone("+1 949 413 6310") == "19494136310"

    def test_is_excluded_exact_match(self):
        """Test exclusion with exact phone number match."""
        mock_client = MagicMock()
        limiter = RateLimiter(
            mock_client,
            daily_limit=100,
            excluded_phones=["+19494136310"],
        )

        assert limiter.is_excluded("+19494136310") is True
        assert limiter.is_excluded("+15551234567") is False

    def test_is_excluded_normalized_match(self):
        """Test exclusion matches normalized phone numbers."""
        mock_client = MagicMock()
        limiter = RateLimiter(
            mock_client,
            daily_limit=100,
            excluded_phones=["+19494136310"],
        )

        # Should match even without + prefix
        assert limiter.is_excluded("19494136310") is True

    def test_is_excluded_empty_list(self):
        """Test no exclusions when list is empty."""
        mock_client = MagicMock()
        limiter = RateLimiter(mock_client, daily_limit=100, excluded_phones=[])

        assert limiter.is_excluded("+19494136310") is False

    def test_is_excluded_none_list(self):
        """Test no exclusions when list is None."""
        mock_client = MagicMock()
        limiter = RateLimiter(mock_client, daily_limit=100, excluded_phones=None)

        assert limiter.is_excluded("+19494136310") is False

    def test_get_date_key_format(self):
        """Test date key format is YYYY-MM-DD."""
        mock_client = MagicMock()
        limiter = RateLimiter(mock_client, daily_limit=100)

        date_key = limiter._get_date_key()
        # Should match format like "2024-01-15"
        assert len(date_key) == 10
        assert date_key[4] == "-"
        assert date_key[7] == "-"

    def test_get_doc_id(self):
        """Test document ID generation."""
        mock_client = MagicMock()
        limiter = RateLimiter(mock_client, daily_limit=100)

        with patch.object(limiter, "_get_date_key", return_value="2024-01-15"):
            doc_id = limiter._get_doc_id("+19494136310")
            assert doc_id == "19494136310_2024-01-15"

    @pytest.mark.asyncio
    async def test_check_and_increment_excluded_phone(self):
        """Test that excluded phones bypass rate limiting."""
        # Use MagicMock for sync collection() call
        mock_collection = MagicMock()
        mock_client = MagicMock()
        mock_client.collection.return_value = mock_collection

        limiter = RateLimiter(
            mock_client,
            daily_limit=100,
            excluded_phones=["+19494136310"],
        )

        is_allowed, count = await limiter.check_and_increment("+19494136310")

        assert is_allowed is True
        assert count == 0
        # Document operations should not be called for excluded phones
        mock_collection.document.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_usage_no_document(self):
        """Test get_usage returns 0 when no document exists."""
        mock_doc = MagicMock()
        mock_doc.exists = False

        mock_doc_ref = MagicMock()
        mock_doc_ref.get = AsyncMock(return_value=mock_doc)

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref

        mock_client = MagicMock()
        mock_client.collection.return_value = mock_collection

        limiter = RateLimiter(mock_client, daily_limit=100)
        usage = await limiter.get_usage("+15551234567")

        assert usage == 0

    @pytest.mark.asyncio
    async def test_get_usage_with_document(self):
        """Test get_usage returns count from existing document."""
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"count": 42}

        mock_doc_ref = MagicMock()
        mock_doc_ref.get = AsyncMock(return_value=mock_doc)

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref

        mock_client = MagicMock()
        mock_client.collection.return_value = mock_collection

        limiter = RateLimiter(mock_client, daily_limit=100)
        usage = await limiter.get_usage("+15551234567")

        assert usage == 42
