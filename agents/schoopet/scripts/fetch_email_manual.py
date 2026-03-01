#!/usr/bin/env python3
"""Manual end-to-end test: fetch a real Gmail message and dump artifacts to disk.

Usage (from project root):
    agents/schoopet/.venv/bin/python agents/schoopet/tests/fetch_email_manual.py <message-id>
"""
import asyncio
import pathlib
import sys

# Project root (schoopet/) must be on sys.path for `agents.schoopet.*` imports.
_ROOT = pathlib.Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(_ROOT))

import dotenv

dotenv.load_dotenv(pathlib.Path(__file__).parent.parent / ".env")

from unittest.mock import AsyncMock

from google.adk.artifacts import InMemoryArtifactService

from agents.schoopet.email_tool import EmailTool

APP_NAME = "schoopet"
USER_ID = "email_system"
SESSION_ID = "manual-test"
OUT_DIR = pathlib.Path(__file__).parent / "out"


def _make_ctx(svc: InMemoryArtifactService) -> AsyncMock:
    ctx = AsyncMock()
    ctx.user_id = USER_ID

    async def save_artifact(filename, artifact):
        await svc.save_artifact(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_ID,
            filename=filename,
            artifact=artifact,
        )

    async def load_artifact(filename, version=None):
        return await svc.load_artifact(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_ID,
            filename=filename,
            version=version,
        )

    ctx.save_artifact = save_artifact
    ctx.load_artifact = load_artifact
    return ctx


async def main(message_id: str) -> None:
    svc = InMemoryArtifactService()
    ctx = _make_ctx(svc)
    tool = EmailTool()

    print(f"Fetching {message_id} …")
    result = await tool.fetch_email(message_id=message_id, tool_context=ctx)
    print("\n── Email ─────────────────────────────────────────────────────")
    print(result)

    keys = await svc.list_artifact_keys(
        app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID
    )
    if not keys:
        print("\nNo artifacts stored.")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n── Artifacts ({len(keys)}) → {OUT_DIR} ──────────────────────────")
    for key in keys:
        part = await svc.load_artifact(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=SESSION_ID,
            filename=key,
        )
        local_name = key.split("_", 1)[1] if "_" in key else key
        out_path = OUT_DIR / local_name
        out_path.write_bytes(part.inline_data.data)
        print(
            f"  {local_name}  ({part.inline_data.mime_type}, "
            f"{len(part.inline_data.data):,} bytes) → {out_path}"
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <gmail-message-id>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
