"""Firestore CRUD for email_workflows collection."""
import logging
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore

logger = logging.getLogger(__name__)

AUTHORIZATIONS_COLLECTION = "email_workflows"


class EmailAuthorizationsClient:
    """Manages email sender → user mappings in Firestore."""

    def __init__(self, firestore_client: firestore.AsyncClient):
        self._db = firestore_client

    def _doc_id(self, sender_email: str) -> str:
        """Derive document ID from sender email."""
        return sender_email.lower().strip()

    async def get_user_for_sender(self, sender_email: str) -> Optional[str]:
        """Return the authorized user phone for a sender, or None."""
        doc_id = self._doc_id(sender_email)
        doc_ref = self._db.collection(AUTHORIZATIONS_COLLECTION).document(doc_id)
        doc = await doc_ref.get()
        if doc.exists:
            return doc.to_dict().get("authorized_user_phone")
        return None

    async def get_authorization(self, sender_email: str) -> Optional[dict]:
        """Return the full authorization document for a sender, or None."""
        doc_id = self._doc_id(sender_email)
        doc_ref = self._db.collection(AUTHORIZATIONS_COLLECTION).document(doc_id)
        doc = await doc_ref.get()
        if doc.exists:
            return doc.to_dict()
        return None

    async def add_authorization(
        self,
        sender_email: str,
        phone: str,
        drive_folder_id: str = "",
        sheet_id: str = "",
        processing_prompt: str = "",
    ) -> None:
        """Create or update a workflow mapping."""
        doc_id = self._doc_id(sender_email)
        doc_ref = self._db.collection(AUTHORIZATIONS_COLLECTION).document(doc_id)
        await doc_ref.set(
            {
                "sender_email": sender_email.lower().strip(),
                "authorized_user_phone": phone,
                "drive_folder_id": drive_folder_id,
                "sheet_id": sheet_id,
                "processing_prompt": processing_prompt,
                "updated_at": datetime.now(timezone.utc),
                "created_at": datetime.now(timezone.utc),
            },
            merge=True,
        )
        logger.info(f"Workflow added: {sender_email} -> {phone[:4]}****")

    async def remove_authorization(self, sender_email: str, phone: str) -> bool:
        """Remove authorization if it belongs to the given phone. Returns True on success."""
        doc_id = self._doc_id(sender_email)
        doc_ref = self._db.collection(AUTHORIZATIONS_COLLECTION).document(doc_id)
        doc = await doc_ref.get()
        if not doc.exists:
            return False
        data = doc.to_dict()
        if data.get("authorized_user_phone") != phone:
            logger.warning(
                f"Attempt to remove authorization {sender_email} by non-owner {phone[:4]}****"
            )
            return False
        await doc_ref.delete()
        logger.info(f"Authorization removed: {sender_email}")
        return True

    async def list_authorizations_for_user(self, phone: str) -> list[dict]:
        """Return all sender authorizations owned by a given phone."""
        query = (
            self._db.collection(AUTHORIZATIONS_COLLECTION)
            .where("authorized_user_phone", "==", phone)
        )
        docs = query.stream()
        results = []
        async for doc in docs:
            results.append(doc.to_dict())
        return results
