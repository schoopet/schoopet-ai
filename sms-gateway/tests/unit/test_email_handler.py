"""Unit tests for the email webhook handler."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.email import handler


# ── _should_suppress_response ─────────────────────────────────────────────────


def test_should_suppress_response_exact_prefix():
    assert handler._should_suppress_response("<SUPPRESS RESPONSE>")


def test_should_suppress_response_with_leading_whitespace():
    assert handler._should_suppress_response("\n  \t<SUPPRESS RESPONSE>\nprocessed")


def test_should_suppress_response_marker_later_does_not_suppress():
    assert not handler._should_suppress_response("Summary\n<SUPPRESS RESPONSE>")


def test_should_suppress_response_normal_message_does_not_suppress():
    assert not handler._should_suppress_response("Here is the summary.")


# ── _format_rules_for_prompt ──────────────────────────────────────────────────


def test_format_rules_includes_channel_directive():
    rules = [
        {
            "topic": "github",
            "sender_filter": "@github.com",
            "prompt": "Summarize in one line.",
            "target_channel_id": "123456789",
        }
    ]
    result = handler._format_rules_for_prompt(rules)
    assert "<CHANNEL:123456789>" in result
    assert "Route to channel" in result


def test_format_rules_without_channel_has_no_route_directive():
    rules = [
        {
            "topic": "invoices",
            "sender_filter": "",
            "prompt": "Notify me.",
        }
    ]
    result = handler._format_rules_for_prompt(rules)
    assert "Route to channel" not in result
    assert "<CHANNEL:" not in result


# ── _atomic_update_last_received ──────────────────────────────────────────────


def _async_transactional_passthrough(f):
    """Stand-in for @firestore.async_transactional that just returns the coroutine."""
    return f


def _make_db_with_doc(doc_data: dict):
    """Build a minimal mock async Firestore client returning doc_data on get()."""
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = doc_data

    doc_ref = MagicMock()
    doc_ref.get = AsyncMock(return_value=doc)
    doc_ref.set = MagicMock()

    transaction = MagicMock()
    transaction.set = MagicMock()

    db = MagicMock()
    db.collection.return_value.document.return_value = doc_ref
    db.transaction.return_value = transaction

    return db, doc_ref, transaction


@pytest.mark.asyncio
async def test_atomic_update_no_pending_sets_sentinel_returns_true(monkeypatch):
    from google.cloud import firestore as fs_module
    monkeypatch.setattr(fs_module, "async_transactional", _async_transactional_passthrough)

    db, doc_ref, transaction = _make_db_with_doc({"user_id": "u1"})
    monkeypatch.setattr(handler, "_db", db)

    result = await handler._atomic_update_last_received("user@gmail.com", "999")

    assert result is True
    transaction.set.assert_called_once_with(
        doc_ref,
        {"last_received_id": "999", "pending_task_id": "SCHEDULING"},
        merge=True,
    )


@pytest.mark.asyncio
async def test_atomic_update_skips_duplicate_history_id(monkeypatch):
    from google.cloud import firestore as fs_module
    monkeypatch.setattr(fs_module, "async_transactional", _async_transactional_passthrough)

    db, _, transaction = _make_db_with_doc({"last_received_id": "1000"})
    monkeypatch.setattr(handler, "_db", db)

    result = await handler._atomic_update_last_received("user@gmail.com", "500")

    assert result is False
    transaction.set.assert_not_called()


@pytest.mark.asyncio
async def test_atomic_update_pending_task_advances_id_but_returns_false(monkeypatch):
    from google.cloud import firestore as fs_module
    monkeypatch.setattr(fs_module, "async_transactional", _async_transactional_passthrough)

    db, doc_ref, transaction = _make_db_with_doc(
        {"last_received_id": "100", "pending_task_id": "task-abc"}
    )
    monkeypatch.setattr(handler, "_db", db)

    result = await handler._atomic_update_last_received("user@gmail.com", "200")

    assert result is False
    # last_received_id advanced, but no SCHEDULING sentinel set
    transaction.set.assert_called_once_with(
        doc_ref,
        {"last_received_id": "200"},
        merge=True,
    )


# ── process_email_notification ────────────────────────────────────────────────


def _make_watch_state(user_id="user-123", discord_channel_id="ch-1"):
    return {
        "user_id": user_id,
        "gmail_address": "user@gmail.com",
        "discord_channel_id": discord_channel_id,
        "last_history_id": "100",
    }


@pytest.mark.asyncio
async def test_process_notification_no_watch_state_returns_early(monkeypatch):
    monkeypatch.setattr(handler, "_db", MagicMock())
    monkeypatch.setattr(
        handler,
        "_read_watch_state",
        AsyncMock(return_value=None),
    )
    task_executor = AsyncMock()
    monkeypatch.setattr(handler, "_task_executor", task_executor)

    await handler.process_email_notification("user@gmail.com", "999")

    task_executor.create_email_batch_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_notification_deduped_creates_no_task(monkeypatch):
    monkeypatch.setattr(handler, "_db", MagicMock())
    monkeypatch.setattr(
        handler,
        "_read_watch_state",
        AsyncMock(return_value=_make_watch_state()),
    )
    monkeypatch.setattr(
        handler,
        "_atomic_update_last_received",
        AsyncMock(return_value=False),
    )
    task_executor = AsyncMock()
    monkeypatch.setattr(handler, "_task_executor", task_executor)

    await handler.process_email_notification("user@gmail.com", "999")

    task_executor.create_email_batch_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_notification_creates_task_and_writes_id(monkeypatch):
    monkeypatch.setattr(
        handler,
        "_read_watch_state",
        AsyncMock(return_value=_make_watch_state(discord_channel_id="ch-42")),
    )
    monkeypatch.setattr(
        handler,
        "_atomic_update_last_received",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(handler, "_load_email_rules", AsyncMock(return_value=[]))

    task_executor = AsyncMock()
    task_executor.create_email_batch_task = AsyncMock(return_value="task-new")
    monkeypatch.setattr(handler, "_task_executor", task_executor)

    doc_ref = MagicMock()
    doc_ref.set = AsyncMock()
    db = MagicMock()
    db.collection.return_value.document.return_value = doc_ref
    monkeypatch.setattr(handler, "_db", db)

    await handler.process_email_notification("user@gmail.com", "999")

    task_executor.create_email_batch_task.assert_awaited_once()
    call_kwargs = task_executor.create_email_batch_task.await_args.kwargs
    assert call_kwargs["gmail_address"] == "user@gmail.com"
    assert call_kwargs["user_id"] == "user-123"
    assert call_kwargs["discord_channel_id"] == "ch-42"
    assert "INCOMING_EMAIL_NOTIFICATION" in call_kwargs["prompt"]

    doc_ref.set.assert_awaited_with({"pending_task_id": "task-new"}, merge=True)


@pytest.mark.asyncio
async def test_process_notification_clears_sentinel_on_task_creation_failure(monkeypatch):
    monkeypatch.setattr(
        handler,
        "_read_watch_state",
        AsyncMock(return_value=_make_watch_state()),
    )
    monkeypatch.setattr(
        handler,
        "_atomic_update_last_received",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(handler, "_load_email_rules", AsyncMock(return_value=[]))

    task_executor = AsyncMock()
    task_executor.create_email_batch_task = AsyncMock(side_effect=Exception("Cloud Tasks down"))
    monkeypatch.setattr(handler, "_task_executor", task_executor)

    doc_ref = MagicMock()
    doc_ref.set = AsyncMock()
    db = MagicMock()
    db.collection.return_value.document.return_value = doc_ref
    monkeypatch.setattr(handler, "_db", db)

    await handler.process_email_notification("user@gmail.com", "999")

    doc_ref.set.assert_awaited_with({"pending_task_id": ""}, merge=True)
