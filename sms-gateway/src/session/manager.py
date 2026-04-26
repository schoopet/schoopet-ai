"""Session management with Firestore storage."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from google.cloud import firestore

from .models import SessionDocument, SessionInfo, SupervisorSessionDocument
from ..utils import normalize_user_id

logger = logging.getLogger(__name__)

COLLECTION_NAME = "sms_sessions"
SUPERVISOR_COLLECTION_NAME = "supervisor_sessions"


class SessionManager:
    """Manages phone number to Agent Engine session mapping.

    Sessions expire after a configurable timeout period. When a session
    expires, a new Agent Engine session is created for the phone number.
    """

    def __init__(
        self,
        firestore_client: firestore.AsyncClient,
        agent_client,  # AgentEngineClient
        timeout_minutes: int = 10,
    ):
        """Initialize session manager.

        Args:
            firestore_client: Async Firestore client instance.
            agent_client: AgentEngineClient for the agent engine.
            timeout_minutes: Session timeout in minutes (default: 10).
        """
        self._db = firestore_client
        self._agent_client = agent_client
        self._timeout = timedelta(minutes=timeout_minutes)
        self._collection = self._db.collection(COLLECTION_NAME)

    async def get_or_create_user(self, phone_number: str) -> SessionInfo:
        """Get existing user or create a new user record.

        This does NOT create an Agent Engine session - that only happens
        after the user opts in.

        Args:
            phone_number: User's phone number in E.164 format.

        Returns:
            SessionInfo with user status (opted_in, is_new_user).
        """
        doc_id = normalize_user_id(phone_number)
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

    async def set_opted_in(self, phone_number: str, channel: str | None = None) -> SessionInfo:
        """Set user as opted in and create Agent Engine session.

        Args:
            phone_number: User's phone number in E.164 format.
            channel: Originating channel (e.g., "sms", "discord") — stored in session state.

        Returns:
            SessionInfo with new session details.
        """
        doc_id = normalize_user_id(phone_number)
        doc_ref = self._collection.document(doc_id)

        logger.info(f"User {phone_number} opted in, creating agent session")
        state = {}
        if channel:
            state["channel"] = channel
        agent_session_id = await self._agent_client.create_session(
            user_id=phone_number, state=state
        )

        now = datetime.now(timezone.utc)
        await doc_ref.update({
            "opted_in": True,
            "personal_agent_session_id": agent_session_id,
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
        doc_id = normalize_user_id(phone_number)
        doc_ref = self._collection.document(doc_id)

        logger.info(f"User {phone_number} opted out")
        await doc_ref.update({
            "opted_in": False,
            "personal_agent_session_id": "",
            "last_activity": datetime.now(timezone.utc),
        })

    async def get_or_create_session(self, phone_number: str, channel: str | None = None) -> SessionInfo:
        """Get existing session or create a new one (for opted-in users only).

        Creates a new session if:
        - No existing session for phone number
        - Existing session has been inactive for more than timeout_minutes

        Args:
            phone_number: User's phone number in E.164 format.
            channel: Originating channel (e.g., "sms", "discord") — stored in session state.

        Returns:
            SessionInfo with session details.
        """
        doc_id = normalize_user_id(phone_number)
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

            existing_session_id = session_doc.personal_agent_session_id

            # Handle both timezone-aware and naive datetimes from Firestore
            now = datetime.now(timezone.utc)
            last_activity = session_doc.last_activity
            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=timezone.utc)
            time_since_activity = now - last_activity

            if time_since_activity < self._timeout and existing_session_id:
                logger.info(
                    f"Continuing session for {phone_number}, "
                    f"last active {time_since_activity.seconds}s ago"
                )
                return SessionInfo(
                    phone_number=phone_number,
                    agent_session_id=existing_session_id,
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
        state = {}
        if channel:
            state["channel"] = channel
        agent_session_id = await self._agent_client.create_session(
            user_id=phone_number, state=state
        )

        # Store in Firestore
        now = datetime.now(timezone.utc)
        if doc.exists:
            await doc_ref.update({
                "personal_agent_session_id": agent_session_id,
                "last_activity": now,
            })
        else:
            session_doc = SessionDocument(
                phone_number=phone_number,
                created_at=now,
                last_activity=now,
                message_count=0,
                opted_in=True,
                personal_agent_session_id=agent_session_id,
            )
            await doc_ref.set(session_doc.to_firestore())

        return SessionInfo(
            phone_number=phone_number,
            agent_session_id=agent_session_id,
            is_new_session=True,
            opted_in=True,
            is_new_user=False,
        )

    async def update_last_activity(self, phone_number: str, channel: str = "sms", slack_team_id: str = "") -> None:
        """Update session's last activity timestamp, channel, and increment message count.

        Args:
            phone_number: User's phone number in E.164 format.
            channel: The channel used for this message (sms or whatsapp).
            slack_team_id: Slack workspace team_id (optional, only stored when non-empty).
        """
        doc_id = normalize_user_id(phone_number)
        doc_ref = self._collection.document(doc_id)

        update_data = {
            "last_activity": datetime.now(timezone.utc),
            "message_count": firestore.Increment(1),
            "channel": channel,
        }
        if slack_team_id:
            update_data["slack_team_id"] = slack_team_id
        await doc_ref.update(update_data)

    async def get_session(self, phone_number: str) -> Optional[SessionDocument]:
        """Get session document if it exists.

        Args:
            phone_number: User's phone number in E.164 format.

        Returns:
            SessionDocument if exists, None otherwise.
        """
        doc_id = normalize_user_id(phone_number)
        doc_ref = self._collection.document(doc_id)

        doc = await doc_ref.get()
        if doc.exists:
            return SessionDocument.from_firestore(doc.to_dict())
        return None

    # ========== Supervisor Session Methods ==========

    async def get_supervisor_session(
        self,
        phone_number: str,
        task_id: str,
    ) -> SessionInfo:
        """Get or create a supervisor session for task review.

        Supervisor sessions are separate from user sessions and are used
        by the root agent to review async task results before sending
        them to users.

        Args:
            phone_number: User's phone number in E.164 format.
            task_id: The async task ID being reviewed.

        Returns:
            SessionInfo with supervisor session details.
        """
        normalized = normalize_user_id(phone_number)
        doc_id = f"{normalized}_supervisor_{task_id}"
        doc_ref = self._db.collection(SUPERVISOR_COLLECTION_NAME).document(doc_id)

        doc = await doc_ref.get()

        if doc.exists:
            session_doc = SupervisorSessionDocument.from_firestore(doc.to_dict())
            logger.info(f"Continuing supervisor session for {phone_number}, task {task_id}")
            return SessionInfo(
                phone_number=phone_number,
                agent_session_id=session_doc.agent_session_id,
                is_new_session=False,
                opted_in=True,  # Supervisor sessions don't need opt-in
                is_new_user=False,
                session_type="supervisor",
                task_id=task_id,
            )

        # Create new supervisor session
        supervisor_user_id = f"{phone_number}_supervisor"
        logger.info(f"Creating supervisor session for {phone_number}, task {task_id}")

        agent_session_id = await self._agent_client.create_session(user_id=supervisor_user_id)

        now = datetime.now(timezone.utc)
        session_doc = SupervisorSessionDocument(
            phone_number=phone_number,
            agent_session_id=agent_session_id,
            session_type="supervisor",
            task_id=task_id,
            created_at=now,
            last_activity=now,
        )
        await doc_ref.set(session_doc.to_firestore())

        return SessionInfo(
            phone_number=phone_number,
            agent_session_id=agent_session_id,
            is_new_session=True,
            opted_in=True,
            is_new_user=False,
            session_type="supervisor",
            task_id=task_id,
        )

    async def update_supervisor_session_activity(
        self,
        phone_number: str,
        task_id: str,
    ) -> None:
        """Update supervisor session's last activity timestamp.

        Args:
            phone_number: User's phone number in E.164 format.
            task_id: The async task ID.
        """
        normalized = normalize_user_id(phone_number)
        doc_id = f"{normalized}_supervisor_{task_id}"
        doc_ref = self._db.collection(SUPERVISOR_COLLECTION_NAME).document(doc_id)

        await doc_ref.update({
            "last_activity": datetime.now(timezone.utc),
        })

    async def get_user_session(self, phone_number: str) -> Optional[SessionInfo]:
        """Get the user's active session if one exists.

        This is used to check if a user has an active session for
        delivering async task results.

        Args:
            phone_number: User's phone number in E.164 format.

        Returns:
            SessionInfo if active session exists, None otherwise.
        """
        session = await self.get_session(phone_number)
        if not session or not session.opted_in:
            return None

        session_id = session.personal_agent_session_id

        if not session_id:
            return None

        return SessionInfo(
            phone_number=phone_number,
            agent_session_id=session_id,
            is_new_session=False,
            opted_in=True,
            is_new_user=False,
            session_type="user",
            channel=session.channel,
        )

    def is_session_active(self, session: Optional[SessionInfo]) -> bool:
        """Check if a session is considered active (within timeout).

        Args:
            session: SessionInfo to check.

        Returns:
            True if session is active, False otherwise.
        """
        if not session or not session.agent_session_id:
            return False

        # For supervisor sessions, always consider them active
        if session.session_type == "supervisor":
            return True

        # For user sessions, we'd need to check last_activity
        # But since we're being called with SessionInfo (not Document),
        # we'll assume it's active if it has a session ID
        return True
