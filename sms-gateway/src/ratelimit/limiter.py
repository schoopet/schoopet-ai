"""Rate limiter with Firestore-backed daily message tracking."""
import logging
from datetime import datetime, timezone
from typing import Set

from google.cloud import firestore

from ..utils import normalize_user_id

logger = logging.getLogger(__name__)

COLLECTION_NAME = "sms_rate_limits"


class RateLimiter:
    """Daily rate limiter for SMS messages.

    Tracks message counts per phone number per day using Firestore.
    Supports an allowlist of phone numbers excluded from rate limiting.
    """

    def __init__(
        self,
        firestore_client: firestore.AsyncClient,
        daily_limit: int = 1000,
        excluded_phones: list[str] = None,
    ):
        """Initialize rate limiter.

        Args:
            firestore_client: Async Firestore client instance.
            daily_limit: Maximum messages per phone number per day.
            excluded_phones: List of phone numbers exempt from rate limiting.
        """
        self._db = firestore_client
        self._daily_limit = daily_limit
        self._excluded_phones: Set[str] = set(excluded_phones or [])
        self._collection = self._db.collection(COLLECTION_NAME)

    def _get_date_key(self) -> str:
        """Get current date as string key (UTC)."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _get_doc_id(self, phone_number: str) -> str:
        """Generate document ID from phone number and date."""
        normalized = normalize_user_id(phone_number)
        date_key = self._get_date_key()
        return f"{normalized}_{date_key}"

    def is_excluded(self, phone_number: str) -> bool:
        """Check if phone number is excluded from rate limiting.

        Args:
            phone_number: Phone number to check.

        Returns:
            True if excluded from rate limiting.
        """
        # Check both with and without + prefix
        normalized = normalize_user_id(phone_number)
        return (
            phone_number in self._excluded_phones
            or f"+{normalized}" in self._excluded_phones
            or normalized in self._excluded_phones
        )

    async def check_and_increment(self, phone_number: str) -> tuple[bool, int]:
        """Check if phone number is within rate limit and increment counter.

        Args:
            phone_number: Phone number to check.

        Returns:
            Tuple of (is_allowed, current_count).
            is_allowed is True if under the limit or excluded.
        """
        # Excluded phones are always allowed
        if self.is_excluded(phone_number):
            logger.debug(f"Phone {phone_number} is excluded from rate limiting")
            return True, 0

        doc_id = self._get_doc_id(phone_number)
        doc_ref = self._collection.document(doc_id)

        # Use transaction for atomic check-and-increment
        @firestore.async_transactional
        async def update_in_transaction(transaction):
            doc = await doc_ref.get(transaction=transaction)

            if doc.exists:
                data = doc.to_dict()
                current_count = data.get("count", 0)
            else:
                current_count = 0

            # Check limit before incrementing
            if current_count >= self._daily_limit:
                return False, current_count

            # Increment counter
            new_count = current_count + 1
            transaction.set(doc_ref, {
                "phone_number": phone_number,
                "date": self._get_date_key(),
                "count": new_count,
                "last_updated": datetime.now(timezone.utc),
            })

            return True, new_count

        transaction = self._db.transaction()
        is_allowed, count = await update_in_transaction(transaction)

        if not is_allowed:
            logger.warning(
                f"Rate limit exceeded for {phone_number}: "
                f"{count}/{self._daily_limit} messages today"
            )
        else:
            logger.debug(f"Rate limit check passed: {phone_number} at {count}/{self._daily_limit}")

        return is_allowed, count

    async def get_usage(self, phone_number: str) -> int:
        """Get current daily usage for a phone number.

        Args:
            phone_number: Phone number to check.

        Returns:
            Current message count for today.
        """
        doc_id = self._get_doc_id(phone_number)
        doc_ref = self._collection.document(doc_id)

        doc = await doc_ref.get()
        if doc.exists:
            return doc.to_dict().get("count", 0)
        return 0
