"""Unit tests for the OAuth handler — covers CUJ-2 (first-time Google OAuth consent)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.oauth import handler
from src.oauth.handler import router


@pytest.fixture(autouse=True)
def reset_handler_globals(monkeypatch):
    monkeypatch.setattr(handler, "_session_manager", None)
    monkeypatch.setattr(handler, "_agent_client", None)
    monkeypatch.setattr(handler, "_discord_sender", None)
    monkeypatch.setattr(handler, "_CREDENTIAL_PROPAGATION_DELAY_SECONDS", 0.0)


@pytest.fixture
def client():
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def services(monkeypatch):
    session_manager = AsyncMock()
    agent_client = AsyncMock()
    discord_sender = AsyncMock()
    monkeypatch.setattr(handler, "_session_manager", session_manager)
    monkeypatch.setattr(handler, "_agent_client", agent_client)
    monkeypatch.setattr(handler, "_discord_sender", discord_sender)
    return session_manager, agent_client, discord_sender


# ── /oauth/authorize ──────────────────────────────────────────────────────────

def test_authorize_redirects_to_auth_uri(client, services):
    session_manager, _, _ = services
    session_manager.get_pending_credential = AsyncMock(
        return_value={"nonce": "abc123", "auth_uri": "https://accounts.google.com/o/oauth2/auth?foo=bar"}
    )

    resp = client.get("/oauth/authorize?nonce=abc123&uid=user-1", follow_redirects=False)

    assert resp.status_code == 307
    assert resp.headers["location"] == "https://accounts.google.com/o/oauth2/auth?foo=bar"
    session_manager.get_pending_credential.assert_called_once_with("user-1")


def test_authorize_returns_404_for_unknown_user(client, services):
    session_manager, _, _ = services
    session_manager.get_pending_credential = AsyncMock(return_value=None)

    resp = client.get("/oauth/authorize?nonce=unknown&uid=no-such-user", follow_redirects=False)

    assert resp.status_code == 404


def test_authorize_returns_404_for_stale_nonce(client, services):
    """Stale link: user_id resolves but stored nonce has been overwritten."""
    session_manager, _, _ = services
    session_manager.get_pending_credential = AsyncMock(
        return_value={"nonce": "new-nonce", "auth_uri": "https://accounts.google.com/..."}
    )

    resp = client.get("/oauth/authorize?nonce=old-nonce&uid=user-1", follow_redirects=False)

    assert resp.status_code == 404


def test_authorize_returns_503_when_not_initialized(client):
    resp = client.get("/oauth/authorize?nonce=abc123&uid=user-1", follow_redirects=False)
    assert resp.status_code == 503


# ── /oauth/connector/callback — uid + state path ──────────────────────────────

def test_callback_uid_path_resumes_agent_and_sends_discord(client, services):
    session_manager, agent_client, discord_sender = services
    session_manager.get_pending_credential = AsyncMock(return_value={
        "nonce": "mynonce",
        "user_id": "user-1",
        "session_id": "sess-1",
        "credential_function_call_id": "fc-1",
        "auth_config_dict": {"scheme": "google"},
    })
    agent_client.send_credential_response = AsyncMock(return_value=[])
    session_manager.clear_pending_credential = AsyncMock()

    with patch("src.oauth.handler.register_gmail_watch", new=AsyncMock()):
        resp = client.get("/oauth/connector/callback?state=mynonce&uid=user-1")

    assert resp.status_code == 200
    assert "Authorization Successful" in resp.text
    session_manager.get_pending_credential.assert_called_once_with("user-1")
    agent_client.send_credential_response.assert_called_once_with(
        user_id="user-1",
        session_id="sess-1",
        credential_function_call_id="fc-1",
        auth_config_dict={"scheme": "google"},
    )
    session_manager.clear_pending_credential.assert_called_once_with("user-1")


def test_callback_missing_uid_returns_400(client, services):
    resp = client.get("/oauth/connector/callback?state=mynonce")
    assert resp.status_code == 400


def test_callback_uid_unknown_returns_400(client, services):
    session_manager, _, _ = services
    session_manager.get_pending_credential = AsyncMock(return_value=None)

    resp = client.get("/oauth/connector/callback?uid=no-such-user")

    assert resp.status_code == 400


# ── /oauth/connector/callback — user_id_validation_state path ────────────────

def test_callback_validation_state_calls_finalize_and_resumes(client, services):
    session_manager, agent_client, discord_sender = services
    session_manager.get_pending_credential = AsyncMock(return_value={
        "nonce": "nonce-xyz",
        "user_id": "user-2",
        "session_id": "sess-2",
        "credential_function_call_id": "fc-2",
        "auth_config_dict": {},
    })
    agent_client.send_credential_response = AsyncMock(return_value=[])
    session_manager.clear_pending_credential = AsyncMock()

    with (
        patch("src.oauth.handler.finalize_iam_credentials", new=AsyncMock()) as mock_finalize,
        patch("src.oauth.handler.register_gmail_watch", new=AsyncMock()),
    ):
        resp = client.get(
            "/oauth/connector/callback"
            "?user_id_validation_state=opaque-state"
            "&connector_name=projects/p/locations/l/connectors/c"
            "&uid=user-2"
        )

    assert resp.status_code == 200
    session_manager.get_pending_credential.assert_called_once_with("user-2")
    mock_finalize.assert_called_once_with(
        connector_name="projects/p/locations/l/connectors/c",
        user_id="user-2",
        consent_nonce="nonce-xyz",
        user_id_validation_state="opaque-state",
    )
    agent_client.send_credential_response.assert_called_once()


def test_callback_validation_state_finalize_failure_returns_500(client, services):
    session_manager, agent_client, _ = services
    session_manager.get_pending_credential = AsyncMock(return_value={
        "nonce": "nonce-xyz",
        "user_id": "user-2",
        "session_id": "sess-2",
        "credential_function_call_id": "fc-2",
        "auth_config_dict": {},
    })

    with patch(
        "src.oauth.handler.finalize_iam_credentials",
        new=AsyncMock(side_effect=RuntimeError("API down")),
    ):
        resp = client.get(
            "/oauth/connector/callback?user_id_validation_state=opaque&connector_name=projects/p/locations/l/connectors/c&uid=user-2"
        )

    assert resp.status_code == 500
    agent_client.send_credential_response.assert_not_called()


def test_callback_missing_params_returns_400(client, services):
    resp = client.get("/oauth/connector/callback")
    assert resp.status_code == 400


def test_callback_oauth_error_param_returns_400(client, services):
    resp = client.get("/oauth/connector/callback?error=access_denied&error_description=User+denied")
    assert resp.status_code == 400
    assert "User denied" in resp.text


# ── import smoke test ─────────────────────────────────────────────────────────

def test_finalize_iam_credentials_importable():
    """Regression: connector.py deletion broke this import (caught by CUJ-2 audit)."""
    from src.auth.connector import finalize_iam_credentials
    assert callable(finalize_iam_credentials)
