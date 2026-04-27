"""Minimal repro for the async mTLS stale-token bug in python-genai.

This script demonstrates two things:

1. Deterministic proof, with no network calls, that
   google.auth.aio.transport.sessions.AsyncAuthorizedSession overwrites the
   Authorization header from its own credentials object.
2. Inspection of a real google.genai.Client configured the same way as the
   Schoopet agent, showing that the async mTLS path is backed by
   StaticCredentials, which never refresh.

Why this matters:
The async mTLS path in google.genai creates:

    AsyncAuthorizedSession(StaticCredentials(token=self._access_token()))

If that session is cached and reused, it can keep applying an old bearer token
even after the underlying ADC credentials have rotated.

Usage:
    ./.venv/bin/python agents/schoopet/scripts/repro_async_mtls_static_token.py

Optional live inspection inside Agent Engine / Cloud Run:
    GOOGLE_GENAI_USE_VERTEXAI=true \
    GOOGLE_CLOUD_PROJECT=schoopet-prod \
    GOOGLE_CLOUD_LOCATION=us-central1 \
    ./.venv/bin/python agents/schoopet/scripts/repro_async_mtls_static_token.py \
      --inspect-live-client
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from typing import Any

from google.auth.aio.credentials import StaticCredentials
from google.auth.aio.transport.sessions import AsyncAuthorizedSession
from google.genai import Client
from google.genai import types


def _fingerprint(value: str | None) -> str:
    if not value:
        return "<missing>"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{value[:12]}... sha256:{digest[:16]}"


async def deterministic_header_overwrite_demo() -> dict[str, Any]:
    """Shows that AsyncAuthorizedSession reapplies its own static token."""
    session = AsyncAuthorizedSession(StaticCredentials("token-from-session"))
    headers = {"authorization": "Bearer token-from-fresh-request"}

    await session._credentials.before_request(  # type: ignore[attr-defined]
        session._auth_request,  # type: ignore[attr-defined]
        "POST",
        "https://aiplatform.mtls.googleapis.com/v1/projects/demo/locations/global/publishers/google/models/gemini-2.5-flash:generateContent",
        headers,
    )

    return {
        "demo": "authorization-header-overwrite",
        "credentials_class": type(session._credentials).__name__,  # type: ignore[attr-defined]
        "header_after_before_request": headers["authorization"],
        "expected": "Bearer token-from-session",
        "overwrites_fresh_header": headers["authorization"]
        == "Bearer token-from-session",
    }


async def inspect_live_genai_client() -> dict[str, Any]:
    """Inspects the real google.genai async mTLS session."""
    client = Client(
        project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        location="global",
        http_options=types.HttpOptions(),
    )
    api_client = client._api_client  # type: ignore[attr-defined]

    fresh_token = api_client._access_token()
    session = await api_client._get_aiohttp_session()

    session_credentials = getattr(session, "_credentials", None)
    session_token = getattr(session_credentials, "token", None)

    headers = {"authorization": "Bearer deliberately-different-token"}
    await session_credentials.before_request(  # type: ignore[union-attr]
        session._auth_request,  # type: ignore[attr-defined]
        "POST",
        "https://aiplatform.mtls.googleapis.com/v1/projects/demo/locations/global/publishers/google/models/gemini-2.5-flash:generateContent",
        headers,
    )

    return {
        "demo": "live-genai-client-inspection",
        "vertexai": api_client.vertexai,
        "base_url": api_client._http_options.base_url,
        "use_aiohttp": api_client._use_aiohttp(),
        "use_google_auth_async": api_client._use_google_auth_async(),
        "session_class": type(session).__name__,
        "session_credentials_class": type(session_credentials).__name__
        if session_credentials
        else None,
        "session_credentials_is_static": isinstance(
            session_credentials, StaticCredentials
        ),
        "fresh_token_fingerprint": _fingerprint(fresh_token),
        "session_token_fingerprint": _fingerprint(session_token),
        "session_token_matches_initial_token": fresh_token == session_token,
        "header_after_before_request": headers["authorization"],
        "session_overwrites_header": headers["authorization"]
        == f"Bearer {session_token}",
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--inspect-live-client",
        action="store_true",
        help="Inspect the real genai client used in an Agent Engine-like env.",
    )
    args = parser.parse_args()

    results = [await deterministic_header_overwrite_demo()]

    if args.inspect_live_client:
        results.append(await inspect_live_genai_client())

    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
