"""Tests for per-resource confirmation decisions."""
import logging
from types import SimpleNamespace

import pytest

from agents.schoopet.resource_confirmation import (
    _RESOURCE_CONFIRMED_PREFIX,
    _approved_resource_ids,
    make_resource_confirmation,
)


class _State(dict):
    def to_dict(self):
        return dict(self)


@pytest.mark.asyncio
async def test_resource_confirmation_approves_matching_resource_id(caplog):
    caplog.set_level(logging.INFO)
    check = make_resource_confirmation("sheet_id")
    state = _State({f"{_RESOURCE_CONFIRMED_PREFIX}sheet-123": True})
    tool_context = SimpleNamespace(state=state, tool_confirmation=None)

    requires_confirmation = await check(
        tool_context=tool_context,
        sheet_id="sheet-123",
    )

    assert requires_confirmation is False
    assert "decision=approved" in caplog.text
    assert "actual_resource_id=sheet-123" in caplog.text
    assert "approved_resource_ids=['sheet-123']" in caplog.text


@pytest.mark.asyncio
async def test_resource_confirmation_requires_confirmation_for_unapproved_resource(caplog):
    caplog.set_level(logging.INFO)
    check = make_resource_confirmation("document_id")
    state = _State({f"{_RESOURCE_CONFIRMED_PREFIX}sheet-123": True})
    tool_context = SimpleNamespace(state=state, tool_confirmation=None)

    requires_confirmation = await check(
        tool_context=tool_context,
        document_id="doc-456",
    )

    assert requires_confirmation is True
    assert "decision=require_confirmation" in caplog.text
    assert "actual_resource_id=doc-456" in caplog.text
    assert "approved_resource_ids=['sheet-123']" in caplog.text


def test_approved_resource_ids_extracts_flat_resource_ids():
    state_keys = [
        "channel",
        f"{_RESOURCE_CONFIRMED_PREFIX}sheet-123",
        f"{_RESOURCE_CONFIRMED_PREFIX}doc-456",
    ]

    assert _approved_resource_ids(state_keys) == ["doc-456", "sheet-123"]
