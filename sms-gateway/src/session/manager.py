"""Session management with Firestore storage."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from google.cloud import firestore

from .models import SessionDocument, SessionInfo

logger = logging.getLogger(__name__)

COLLECTION_NAME = "sms_sessions"


class SessionManager:
    """Manages phone number to Agent Engine session mapping.

    Sessions expire after a configurable timeout period. When a session
    expires, a new Agent Engine session is created for the phone number.
    """

    def __init__(
        self,
        firestore_client: firestore.AsyncClient,
        agent_client,  # AgentEngineClient - imported at runtime to avoid circular imports
        timeout_minutes: int = 10,
    ):
        """Initialize session manager.

        Args:
            firestore_client: Async Firestore client instance.
            agent_client: AgentEngineClient for creating new sessions.
            timeout_minutes: Session timeout in minutes (default: 10).
        """
        self._db = firestore_client
        self._agent_client = agent_client
        self._timeout = timedelta(minutes=timeout_minutes)
        self._collection = self._db.collection(COLLECTION_NAME)

    def _normalize_phone(self, phone_number: str) -> str:
        """Normalize phone number for consistent document IDs.

        Removes any characters that aren't valid in Firestore document IDs.
        """
        # Remove + prefix and any non-alphanumeric chars
        return phone_number.lstrip("+").replace("-", "").replace(" ", "")

    async def get_or_create_user(self, phone_number: str) -> SessionInfo:
        """Get existing user or create a new user record.

        This does NOT create an Agent Engine session - that only happens
        after the user opts in.

        Args:
            phone_number: User's phone number in E.164 format.

        Returns:
            SessionInfo with user status (opted_in, is_new_user).
        """
        doc_id = self._normalize_phone(phone_number)
        doc_ref = self._collection.document(doc_id)

        doc = await doc_ref.get()

        if doc.exists:
            session_doc = SessionDocument.from_firestore(doc.to_dict())
            return SessionInfo(
                phone_number=phone_number,
                agent_session_id=session_doc.agent_session_id,
                is_new_session=False,
                opted_in=session_doc.opted_in,
                is_new_user=False,
            )

        # Create new user record (not opted in yet)
        logger.info(f"Creating new user record for {phone_number}")
        now = datetime.now(timezone.utc)
        session_doc = SessionDocument(
            phone_number=phone_number,
            agent_session_id="",
            created_at=now,
            last_activity=now,
            message_count=0,
            opted_in=False,
            opt_in_requested_at=now,
        )
        await doc_ref.set(session_doc.to_firestore())

        return SessionInfo(
            phone_number=phone_number,
            agent_session_id="",
            is_new_session=False,
            opted_in=False,
            is_new_user=True,
        )

    async def set_opted_in(self, phone_number: str) -> SessionInfo:
        """Set user as opted in and create Agent Engine session.

        Args:
            phone_number: User's phone number in E.164 format.

        Returns:
            SessionInfo with new session details.
        """
        doc_id = self._normalize_phone(phone_number)
        doc_ref = self._collection.document(doc_id)

        # Create Agent Engine session
        logger.info(f"User {phone_number} opted in, creating agent session")
        agent_session_id = await self._agent_client.create_session(user_id=phone_number)

        now = datetime.now(timezone.utc)
        await doc_ref.update({
            "opted_in": True,
            "agent_session_id": agent_session_id,
            "last_activity": now,
        })

        return SessionInfo(
            phone_number=phone_number,
            agent_session_id=agent_session_id,
            is_new_session=True,
            opted_in=True,
            is_new_user=False,
        )

    async def set_opted_out(self, phone_number: str) -> None:
        """Set user as opted out.

        Args:
            phone_number: User's phone number in E.164 format.
        """
        doc_id = self._normalize_phone(phone_number)
        doc_ref = self._collection.document(doc_id)

        logger.info(f"User {phone_number} opted out")
        await doc_ref.update({
            "opted_in": False,
            "agent_session_id": "",
            "last_activity": datetime.now(timezone.utc),
        })

    async def get_or_create_session(self, phone_number: str) -> SessionInfo:
        """Get existing session or create a new one (for opted-in users only).

        Creates a new session if:
        - No existing session for phone number
        - Existing session has been inactive for more than timeout_minutes

        Args:
            phone_number: User's phone number in E.164 format.

        Returns:
            SessionInfo with session details.
        """
        doc_id = self._normalize_phone(phone_number)
        doc_ref = self._collection.document(doc_id)

        doc = await doc_ref.get()

        if doc.exists:
            session_doc = SessionDocument.from_firestore(doc.to_dict())

            # Check if user is opted in
            if not session_doc.opted_in:
                return SessionInfo(
                    phone_number=phone_number,
                    agent_session_id="",
                    is_new_session=False,
                    opted_in=False,
                    is_new_user=False,
                )

            # Handle both timezone-aware and naive datetimes from Firestore
            now = datetime.now(timezone.utc)
            last_activity = session_doc.last_activity
            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=timezone.utc)
            time_since_activity = now - last_activity

            if time_since_activity < self._timeout and session_doc.agent_session_id:
                logger.info(
                    f"Continuing session for {phone_number}, "
                    f"last active {time_since_activity.seconds}s ago"
                )
                return SessionInfo(
                    phone_number=phone_number,
                    agent_session_id=session_doc.agent_session_id,
                    is_new_session=False,
                    opted_in=True,
                    is_new_user=False,
                )
            else:
                logger.info(
                    f"Session expired for {phone_number}, "
                    f"inactive for {time_since_activity.seconds}s"
                )

        # Create new Agent Engine session
        logger.info(f"Creating new session for {phone_number}")
        agent_session_id = await self._agent_client.create_session(user_id=phone_number)

        # Store in Firestore
        now = datetime.now(timezone.utc)
        if doc.exists:
            await doc_ref.update({
                "agent_session_id": agent_session_id,
                "last_activity": now,
            })
        else:
            session_doc = SessionDocument(
                phone_number=phone_number,
                agent_session_id=agent_session_id,
                created_at=now,
                last_activity=now,
                message_count=0,
                opted_in=True,
            )
            await doc_ref.set(session_doc.to_firestore())

        return SessionInfo(
            phone_number=phone_number,
            agent_session_id=agent_session_id,
            is_new_session=True,
            opted_in=True,
            is_new_user=False,
        )

    async def update_last_activity(self, phone_number: str) -> None:
        """Update session's last activity timestamp and increment message count.

        Args:
            phone_number: User's phone number in E.164 format.
        """
        doc_id = self._normalize_phone(phone_number)
        doc_ref = self._collection.document(doc_id)

        await doc_ref.update({
            "last_activity": datetime.now(timezone.utc),
            "message_count": firestore.Increment(1),
        })

    async def get_session(self, phone_number: str) -> Optional[SessionDocument]:
        """Get session document if it exists.

        Args:
            phone_number: User's phone number in E.164 format.

        Returns:
            SessionDocument if exists, None otherwise.
        """
        doc_id = self._normalize_phone(phone_number)
        doc_ref = self._collection.document(doc_id)

        doc = await doc_ref.get()
        if doc.exists:
            return SessionDocument.from_firestore(doc.to_dict())
        return None
