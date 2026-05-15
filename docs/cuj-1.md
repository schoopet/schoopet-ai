# CUJ 1: Discord DM / @mention → agent response

## Step 1 — Process start: WebSocket connection, not HTTP

The gateway is not a webhook endpoint. `start_gateway()` (`discord/gateway.py:524`) creates a `SchoopetGateway` (subclasses `discord.Client`) and launches `_run_gateway_with_retry()` as a background asyncio task at startup. This is a persistent WebSocket to Discord's gateway servers. On disconnect it retries with exponential backoff capped at 5 minutes (`gateway.py:512-521`).

Intents registered: `dm_messages`, `guild_messages` (both non-privileged), and `message_content` (privileged — must be enabled in the Discord Developer Portal manually).

---

## Step 2 — `on_message()` (`gateway.py:185`)

Filtering:
- Ignores messages from itself or any other bot (`message.author == self.user or message.author.bot`)
- If it's a guild `@mention`, strips the mention prefix (`<@{id}>` and `<@!{id}>` variants) from the text
- Drops messages with no text AND no attachments — a bare reaction or embed with no content body is silently ignored

User ID is `str(message.author.id)` — a Discord snowflake, not a phone number. The `phone_number` parameter name used throughout `SessionManager` is a naming artifact from the SMS origins.

**`DiscordContext` is built twice from `message.channel`** — once at line 207 (for logging) and again at line 239 (passed into `_handle_gateway_message`). Harmless but redundant.

Attachment handling (`_build_discord_message_content`, `gateway.py:66`):
- Each attachment is fetched with `await attachment.read()` and embedded as `types.Blob(mime_type, data)`
- Hard limit: 10 MB per attachment — raises `ValueError` if exceeded, which is caught and sent to the user
- If attachments exist and the user provided text, the text becomes the `parts[0]` prefix; if no text, a fallback string is inserted
- Returns a `types.Content(role="user", parts=[...])`

**Timing gap**: `_build_discord_message_content()` runs at line 214, but the typing indicator task isn't started until line 233. If the user sends a large attachment, attachment download happens before the typing indicator appears — the user sees no feedback during that window.

After attachments are read, the typing indicator is started as a background task (`asyncio.create_task`) that holds `channel.typing()` open for up to 300 seconds. The `finally` block cancels it when `_handle_gateway_message` returns.

---

## Step 3 — Rate limit (`gateway.py:326`, `ratelimit/limiter.py:67`)

`RateLimiter.check_and_increment()` runs a Firestore transaction on `sms_rate_limits/{user_id}_{YYYY-MM-DD}`. Atomically reads the count, rejects if at/over the daily limit, otherwise increments. Excluded user IDs (configured as `RATE_LIMIT_EXCLUDED_PHONES`) bypass this entirely.

---

## Step 4 — Session lookup (`session/manager.py:183`)

`get_or_create_session()` is called with `channel="discord"`, `session_scope`, and `state_extra` from `DiscordContext`.

**Document ID**: `_doc_id()` (`manager.py:46`) — if `session_scope` is set (it always is for Discord), the doc ID is `{normalized_user_id}__{sha256(session_scope)[:16]}`. This means:
- Each guild channel gets its **own Firestore doc and its own Agent Engine session** — conversations in `#channel-a` and `#channel-b` are independent
- DMs use `discord:dm:{channel_id}` as the scope, where `channel_id` is the DM channel ID (stable per user, but not the user ID itself)

Session expiry logic:
- If the doc exists and `last_activity` is within 30 min (`SESSION_TIMEOUT_MINUTES`): reuses the existing Agent Engine session — no new session created
- If expired or no doc: deletes the old Agent Engine session (best-effort, failure is swallowed), then creates a new one
- New session state includes `channel`, `session_scope`, and the Discord context fields (`discord_channel_id`, `discord_guild_id`, `discord_channel_name`) stored in both the Agent Engine session state and Firestore

**No `opted_in` check** — unlike the original SMS flow, Discord users are never gated. The doc for a new Discord user is created with `opted_in=True` on first message (`manager.py:280`).

---

## Step 5 — Message send (`agent/client.py:203`)

`wrap_message_with_discord_context()` (`context.py:73`) prepends a context block to the message seen by the agent:
```
Discord context:
session_scope: discord:dm:1234567890
channel_id: 1234567890
```
For a `types.Content` (multimodal), it injects this into the first text part, or inserts a new part at position 0.

`send_message_events()` streams from `async_stream_query()`. Events can arrive as raw dicts or `Event` objects — both paths are handled. On a `dict` event with `code=401`, the Vertex AI client is reinitialized (`_init_client()`) and the stream is retried once. Timeout is applied via `asyncio.wait_for` — the whole stream must complete within `AGENT_TIMEOUT_SECONDS` (default 900s). A `TimeoutError` propagates back to `_handle_gateway_message` which catches it and sends a user-facing message (`gateway.py:354`).

Tool call logging: every function call and response is logged at INFO with the first 120 chars of each string arg and first 200 chars of each response.

---

## Step 6 — Event dispatch (`gateway.py:362-414`)

Three-way priority check, in order:

1. **`extract_credential_requests()`** — scans all events for `function_call.name == "adk_request_credential"`. If found, bails out of the normal response path entirely (→ CUJ 2).

2. **`extract_confirmation_requests()`** — scans for `function_call.name == "adk_request_confirmation"`. If found, stores each in Firestore via `add_pending_approval()` and sends Discord button messages (→ CUJ 5). Also bails out.

3. **`extract_text()`** — concatenates all `event.content.parts[*].text` across all events into one string. If non-empty, sends it. If empty (which can happen when the agent only made tool calls with no final text turn), sends "I couldn't generate a response."

---

## Step 7 — Response delivery (`gateway.py:244`, `sender.py:121`)

`_send_channel_text()` delegates to `_split_message()`, which splits at newlines first, then spaces, then hard-cuts — keeping each chunk under Discord's 2000-character limit. Multiple chunks are sent as sequential `channel.send()` calls.

`update_last_activity()` writes the new timestamp to Firestore after the response is sent.

---

## Issues the code trace surfaces

**1. `context_from_discord_channel()` called twice on `message.channel`** — lines 207 and 239. The second call is passed in but the `_handle_gateway_message` default-arg at line 323 would also call it a third time if `None` were passed.

**2. Typing indicator starts after attachment download** — if a user sends a 9 MB image, several seconds pass with no "typing..." shown. The typing task should start before `_build_discord_message_content`.

**3. No message deduplication** — if Discord delivers the same event twice (which can happen after reconnects), both will be forwarded to the agent in the same session, producing a duplicate response.

**4. Chained confirmations use interaction followup** (`gateway.py:488-493`) — when a confirmation button click triggers a second confirmation, it sends via `interaction.followup`. Discord interaction tokens expire after 15 minutes, so a multi-step confirmation chain that takes longer will fail silently.

**5. `session_scope` SHA-256 truncated to 16 hex chars** — 64 bits of collision resistance. Extremely unlikely to collide in practice.
