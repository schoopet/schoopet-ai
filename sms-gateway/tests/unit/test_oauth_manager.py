from unittest.mock import AsyncMock, MagicMock

import pytest

from src.oauth.manager import OAuthManager


@pytest.mark.asyncio
async def test_store_tokens_rolls_back_firestore_when_refresh_token_persist_fails():
    firestore_client = MagicMock()
    secret_manager = AsyncMock()
    secret_manager.store_refresh_token.return_value = False

    doc_ref = AsyncMock()
    tokens_collection = MagicMock()
    tokens_collection.document.return_value = doc_ref
    firestore_client.collection.return_value = tokens_collection

    manager = OAuthManager(
        firestore_client=firestore_client,
        secret_manager=secret_manager,
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://example.com/oauth/google/callback",
    )

    stored = await manager.store_tokens(
        user_id="+14155551234",
        email="user@example.com",
        access_token="access-token",
        refresh_token="refresh-token",
        expires_in=3600,
        feature="google",
    )

    assert stored is False
    doc_ref.set.assert_awaited_once()
    secret_manager.store_refresh_token.assert_awaited_once()
    doc_ref.delete.assert_awaited_once()
