"""Session management with Firestore storage."""
import logging
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from google.cloud import firestore

from .models import SessionDocument, SessionInfo
from .approvals import PendingApprovalCoordinator
from ..utils import normalize_user_id

logger = logging.getLogger(__name__)

COLLECTION_NAME = "sms_sessions"
PENDING_CREDENTIALS_COLLECTION = "pending_credentials"


class SessionManager:
    """Manages user ID to Agent Engine session mapping.

    Sessions expire after a configurable timeout period. When a session
    expires, a new Agent Engine session is created for the user.
    """

    def __init__(
        self,
        firestore_client: firestore.AsyncClient,
        agent_client,  # AgentEngineClient
        timeout_minutes: int = 30,
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
        self._approval_coordinator = PendingApprovalCoordinator()

    def _doc_id(self, user_id: str, session_scope: str | None = None) -> str:
        """Return the Firestore document ID for a user/session scope."""
        normalized = normalize_user_id(user_id)
        if not session_scope:
            return normalized
        scope_hash = hashlib.sha256(session_scope.encode("utf-8")).hexdigest()[:16]
        return f"{normalized}__{scope_hash}"

    @staticmethod
    def _build_session_state(
        channel: str | None,
        session_scope: str | None = None,
        state_extra: dict | None = None,
    ) -> dict:
        state = dict(state_extra or {})
        if channel:
            state["channel"] = channel
        if session_scope:
            state["session_scope"] = session_scope
        return state

    async def get_or_create_user(self, phone_number: str) -> SessionInfo:
        """Get existing user or create a new user record.

        This does NOT create an Agent Engine session - that only happens
        after the user opts in.

        Args:
            phone_number: User identifier.

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
            phone_number: User identifier.
            channel: Originating channel, stored in agent session state and on the Firestore session doc.

        Returns:
            SessionInfo with new session details.
        """
        doc_id = normalize_user_id(phone_number)
        doc_ref = self._collection.document(doc_id)

        logger.info(f"User {phone_number} opted in, creating agent session")
        state: dict = {}
        if channel:
            state["channel"] = channel
        agent_session_id = await self._agent_client.create_session(
            user_id=phone_number, state=state
        )

        now = datetime.now(timezone.utc)
        update_fields = {
            "opted_in": True,
            "personal_agent_session_id": agent_session_id,
            "last_activity": now,
        }
        if channel:
            update_fields["channel"] = channel
        await doc_ref.update(update_fields)

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
            phone_number: User identifier.
        """
        doc_id = normalize_user_id(phone_number)
        doc_ref = self._collection.document(doc_id)

        existing = await self.get_session(phone_number)
        if existing and existing.personal_agent_session_id:
            await self._retire_session(phone_number, existing.personal_agent_session_id)

        logger.info(f"User {phone_number} opted out")
        await doc_ref.update({
            "opted_in": False,
            "personal_agent_session_id": "",
            "last_activity": datetime.now(timezone.utc),
        })

    async def _retire_session(self, user_id: str, session_id: str) -> None:
        """Delete a session when it is no longer active.

        Memory ingestion is handled inside the agent via ADK callbacks.
        Session retirement here is cleanup only. Best-effort.
        """
        await self._agent_client.delete_session(user_id, session_id)

    async def get_or_create_session(
        self,
        phone_number: str,
        channel: str,
        session_scope: str | None = None,
        state_extra: dict | None = None,
    ) -> SessionInfo:
        """Get existing session or create a new one.

        Creates a new Agent Engine session if:
        - No existing session document for the user
        - Existing session has been inactive for more than timeout_minutes

        Does NOT check `opted_in` — callers that need explicit opt-in gating must
        check it themselves via `get_or_create_user()`.

        Args:
            phone_number: User identifier.
            channel: Originating channel, stored in agent session state and on the Firestore session doc.
            session_scope: Optional channel/thread scope for separate concurrent sessions.
            state_extra: Optional metadata stored in session state and Firestore.

        Returns:
            SessionInfo with `is_new_user` set when the Firestore doc was just created.
        """
        doc_id = self._doc_id(phone_number, session_scope)
        doc_ref = self._collection.document(doc_id)

        doc = await doc_ref.get()
        is_new_user = not doc.exists
        stale_session_id: str | None = None

        if doc.exists:
            session_doc = SessionDocument.from_firestore(doc.to_dict())
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
                    opted_in=session_doc.opted_in,
                    is_new_user=False,
                    channel=session_doc.channel,
                    session_scope=session_doc.session_scope,
                    state_extra=session_doc.state_extra,
                )
            else:
                logger.info(
                    f"Session expired for {phone_number}, "
                    f"inactive for {time_since_activity.seconds}s"
                )
                if existing_session_id:
                    stale_session_id = existing_session_id

        # Retire the stale session before creating the replacement.
        if stale_session_id:
            await self._retire_session(phone_number, stale_session_id)

        # Create new Agent Engine session
        logger.info(f"Creating new session for {phone_number} scope={session_scope or 'default'}")
        state = self._build_session_state(channel, session_scope, state_extra)
        agent_session_id = await self._agent_client.create_session(
            user_id=phone_number, state=state
        )

        # Store in Firestore
        now = datetime.now(timezone.utc)
        if doc.exists:
            update_fields = {
                "personal_agent_session_id": agent_session_id,
                "last_activity": now,
                "session_scope": session_scope or "",
                "state_extra": state_extra or {},
            }
            if channel:
                update_fields["channel"] = channel
            await doc_ref.update(update_fields)
        else:
            session_doc = SessionDocument(
                phone_number=phone_number,
                created_at=now,
                last_activity=now,
                message_count=0,
                opted_in=True,
                personal_agent_session_id=agent_session_id,
                channel=channel or "discord",
                session_scope=session_scope or "",
                state_extra=state_extra or {},
            )
            await doc_ref.set(session_doc.to_firestore())

        return SessionInfo(
            phone_number=phone_number,
            agent_session_id=agent_session_id,
            is_new_session=True,
            opted_in=True,
            is_new_user=is_new_user,
            channel=channel or "discord",
            session_scope=session_scope or "",
            state_extra=state_extra or {},
        )

    async def update_last_activity(
        self,
        phone_number: str,
        channel: str,
        session_scope: str | None = None,
    ) -> None:
        """Update session's last activity timestamp, channel, and increment message count.

        Args:
            phone_number: User identifier.
            channel: The channel used for this message.
        """
        doc_id = self._doc_id(phone_number, session_scope)
        doc_ref = self._collection.document(doc_id)

        update_data = {
            "last_activity": datetime.now(timezone.utc),
            "message_count": firestore.Increment(1),
            "channel": channel,
        }
        await doc_ref.update(update_data)

    async def get_session(
        self,
        phone_number: str,
        session_scope: str | None = None,
    ) -> Optional[SessionDocument]:
        """Get session document if it exists.

        Args:
            phone_number: User identifier.

        Returns:
            SessionDocument if exists, None otherwise.
        """
        doc_id = self._doc_id(phone_number, session_scope)
        doc_ref = self._collection.document(doc_id)

        doc = await doc_ref.get()
        if doc.exists:
            return SessionDocument.from_firestore(doc.to_dict())
        return None

    async def add_pending_approval(
        self,
        user_id: str,
        confirmation,
        session_id: str,
        channel: str,
        session_scope: str | None = None,
    ) -> dict:
        """Append one live pending ADK approval to the user's approval list.

        These approvals are interactive prompts stored on the session document.
        Offline/background tasks do not use this API; they use
        allowed_resource_ids to pre-authorize resource IDs before execution.
        """
        doc_id = self._doc_id(user_id, session_scope)
        doc_ref = self._collection.document(doc_id)
        pending_id = secrets.token_urlsafe(9)
        confirmation_data = (
            confirmation.to_firestore()
            if hasattr(confirmation, "to_firestore")
            else dict(confirmation)
        )
        existing_doc = await doc_ref.get()
        existing_data = existing_doc.to_dict() if existing_doc.exists is True else {}
        existing_pending = []
        if isinstance(existing_data, dict):
            existing_pending = existing_data.get("pending_confirmations", []) or []

        pending = self._approval_coordinator.build_pending(
            confirmation_data=confirmation_data,
            existing_pending=existing_pending,
            pending_id=pending_id,
            user_id=user_id,
            session_id=session_id,
            channel=channel,
            session_scope=session_scope or "",
            created_at=datetime.now(timezone.utc),
        )
        await doc_ref.update({"pending_confirmations": firestore.ArrayUnion([pending])})
        return pending

    async def get_pending_approval(
        self,
        user_id: str,
        pending_id: str,
        session_scope: str | None = None,
    ) -> Optional[dict]:
        """Return the pending approval matching pending_id, or None."""
        session = await self.get_session(user_id, session_scope=session_scope)
        if not session:
            return None
        return next(
            (c for c in session.pending_confirmations if c.get("id") == pending_id),
            None,
        )

    async def get_pending_approval_group(
        self,
        user_id: str,
        pending_id: str,
        session_scope: str | None = None,
    ) -> list[dict]:
        """Return all pending approvals sharing pending_id's approval group."""
        session = await self.get_session(user_id, session_scope=session_scope)
        if not session:
            return []

        return self._approval_coordinator.group_for_pending_id(
            session.pending_confirmations,
            pending_id,
        )

    async def clear_pending_approval(
        self,
        user_id: str,
        pending_id: str,
        session_scope: str | None = None,
    ) -> None:
        """Remove one pending approval by ID from the user's approval list."""
        session = await self.get_session(user_id, session_scope=session_scope)
        if not session:
            return
        remaining = [c for c in session.pending_confirmations if c.get("id") != pending_id]
        doc_id = self._doc_id(user_id, session_scope)
        doc_ref = self._collection.document(doc_id)
        await doc_ref.update({"pending_confirmations": remaining})

    async def clear_pending_approval_group(
        self,
        user_id: str,
        pending_id: str,
        session_scope: str | None = None,
    ) -> None:
        """Remove all pending approvals sharing pending_id's approval group."""
        session = await self.get_session(user_id, session_scope=session_scope)
        if not session:
            return

        remaining = self._approval_coordinator.clear_group_for_pending_id(
            session.pending_confirmations,
            pending_id,
        )
        doc_id = self._doc_id(user_id, session_scope)
        doc_ref = self._collection.document(doc_id)
        await doc_ref.update({"pending_confirmations": remaining})

    def should_send_pending_approval_notification(self, pending_group: list[dict]) -> bool:
        """Return whether a grouped pending approval should emit a user prompt."""
        return self._approval_coordinator.should_send_notification(pending_group)

    def pending_approval_notification_id(self, pending_group: list[dict]) -> str:
        """Return the pending ID that owns the grouped approval button."""
        return self._approval_coordinator.notification_id(pending_group)

    def format_pending_approval_notification(self, pending_group: list[dict]) -> str:
        """Return user-facing text for a pending approval group."""
        return self._approval_coordinator.format_notification(pending_group)

    async def get_user_session(
        self,
        phone_number: str,
        session_scope: str | None = None,
    ) -> Optional[SessionInfo]:
        """Get the user's active session if one exists.

        This is used to check if a user has an active session for
        delivering async task results.

        Args:
            phone_number: User identifier.

        Returns:
            SessionInfo if active session exists, None otherwise.
        """
        session = await self.get_session(phone_number, session_scope=session_scope)
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
            channel=session.channel,
            session_scope=session.session_scope,
            state_extra=session.state_extra,
        )

    def is_session_active(self, session: Optional[SessionInfo]) -> bool:
        """Check if a session is considered active."""
        return bool(session and session.agent_session_id)

    # ── IAM connector pending credentials ────────────────────────────────────

    async def set_pending_credential(
        self,
        nonce: str,
        user_id: str,
        session_id: str,
        credential_function_call_id: str,
        auth_config_dict: dict,
        auth_uri: str,
    ) -> None:
        """Store a pending IAM connector credential request keyed by nonce."""
        from datetime import datetime, timezone
        doc_ref = self._db.collection(PENDING_CREDENTIALS_COLLECTION).document(nonce)
        await doc_ref.set({
            "nonce": nonce,
            "user_id": user_id,
            "session_id": session_id,
            "credential_function_call_id": credential_function_call_id,
            "auth_config_dict": auth_config_dict,
            "auth_uri": auth_uri,
            "created_at": datetime.now(timezone.utc),
        })

    async def get_pending_credential(self, nonce: str) -> Optional[dict]:
        """Retrieve a pending credential record by nonce."""
        try:
            doc = await self._db.collection(PENDING_CREDENTIALS_COLLECTION).document(nonce).get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            logger.error(f"Failed to get pending credential for nonce {nonce[:8]}...: {e}")
            return None

    async def get_latest_pending_credential(self) -> Optional[tuple[str, dict]]:
        """Return (nonce, data) for the most recently created pending credential.

        Used when the IAM connector callback does not echo the nonce back
        (it sends user_id_validation_state instead, which is opaque to us).
        Safe for single-user deployments.
        """
        try:
            query = (
                self._db.collection(PENDING_CREDENTIALS_COLLECTION)
                .order_by("created_at", direction=firestore.Query.DESCENDING)
                .limit(1)
            )
            async for doc in query.stream():
                return doc.id, doc.to_dict()
            return None, None
        except Exception as e:
            logger.error(f"Failed to get latest pending credential: {e}")
            return None, None

    async def clear_pending_credential(self, nonce: str) -> None:
        """Remove a pending credential record."""
        try:
            await self._db.collection(PENDING_CREDENTIALS_COLLECTION).document(nonce).delete()
        except Exception as e:
            logger.error(f"Failed to clear pending credential for nonce {nonce[:8]}...: {e}")
