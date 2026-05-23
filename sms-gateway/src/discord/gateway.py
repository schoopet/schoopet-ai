"""Discord Gateway client for DM and @mention support.

Connects to Discord's WebSocket gateway so users can talk to the agent
by DMing the bot directly or @mentioning it in a server — no slash command
needed.

Requires the following Gateway intents (enable in Discord Developer Portal
under Bot → Privileged Gateway Intents):
  - MESSAGE CONTENT  (privileged — needed to read message text)

The non-privileged DIRECT_MESSAGES and GUILD_MESSAGES intents are requested
in code and do not need manual enabling.
"""
import asyncio
import logging
import random
import time

import discord
from google.genai import types

from ..agent.client import AgentEngineClient as _AgentEngineClient
from .context import (
    DiscordContext,
    context_from_discord_channel,
    wrap_message_with_discord_context,
)
from .sender import _split_message
from ..messages import RATE_LIMIT_MSG

logger = logging.getLogger(__name__)

MAX_INLINE_ATTACHMENT_BYTES = 10 * 1024 * 1024
FALLBACK_MIME_TYPE = "application/octet-stream"

_TOOL_LABELS: dict[str, str] = {
    # Memory
    "load_memory": "Checking memory",
    "preload_memory": "Checking memory",
    "save_memory": "Saving to memory",
    "save_multiple_memories": "Saving to memory",
    # Calendar
    "list_calendar_events": "Checking calendar",
    "create_calendar_event": "Creating calendar event",
    "update_calendar_event": "Updating calendar event",
    "delete_calendar_event": "Updating calendar",
    "get_calendar_status": "Checking calendar",
    # Drive
    "save_file_to_drive": "Saving to Drive",
    "save_attachment_to_drive": "Saving to Drive",
    "list_drive_files": "Searching Drive",
    "get_drive_status": "Checking Drive",
    # Docs
    "create_google_doc": "Creating document",
    "read_google_doc": "Reading document",
    "append_to_google_doc": "Updating document",
    "replace_text_in_google_doc": "Updating document",
    "get_docs_status": "Checking Docs",
    # Sheets
    "create_spreadsheet": "Creating spreadsheet",
    "add_sheet_tab": "Updating spreadsheet",
    "get_sheet_schema": "Reading spreadsheet",
    "read_sheet_records": "Reading spreadsheet",
    "ensure_sheet_headers": "Updating spreadsheet",
    "append_record_to_sheet": "Updating spreadsheet",
    "find_sheet_rows": "Searching spreadsheet",
    "update_sheet_row": "Updating spreadsheet",
    "append_row_to_sheet": "Updating spreadsheet",
    "read_sheet": "Reading spreadsheet",
    "add_sheet_column": "Updating spreadsheet",
    "update_sheet_cell": "Updating spreadsheet",
    "get_sheets_status": "Checking Sheets",
    # Gmail
    "atomic_read_received_emails": "Reading email",
    "read_emails": "Reading email",
    "fetch_email": "Reading email",
    "list_artifacts": "Checking email attachments",
    "read_artifact": "Reading email attachment",
    "get_gmail_status": "Checking Gmail",
    "setup_gmail_watch": "Configuring Gmail",
    "add_email_rule": "Updating email rules",
    "list_email_rules": "Checking email rules",
    "update_email_rule": "Updating email rules",
    "remove_email_rule": "Updating email rules",
    # Time & preferences
    "get_current_time": "Checking time",
    "convert_time": "Converting time",
    "parse_natural_datetime": "Parsing date",
    "next_occurrence": "Calculating schedule",
    "set_timezone": "Updating timezone",
    "get_timezone": "Checking timezone",
    # Async tasks
    "create_async_task": "Scheduling task",
    "check_task_status": "Checking task",
    "get_task_result": "Fetching task result",
    "cancel_task": "Cancelling task",
    "list_pending_tasks": "Listing tasks",
    # Task debug
    "get_cloud_task_status": "Checking task",
    "list_scheduled_tasks": "Listing tasks",
    "debug_task": "Debugging task",
    # Discord/misc
    "list_discord_channels": "Checking Discord channels",
    "check_connector_credential": "Checking credentials",
    # Subagents
    "search_agent": "Searching the web",
    "code_executor": "Running code",
}


def _format_tool_name(name: str) -> str:
    return _TOOL_LABELS.get(name) or name.replace("_", " ").title()


_CAT_STARTERS = ["> mrrp...", "> *purring*...", "> prrr...", "> mew..."]
_CAT_SUFFIXES = [" mrrp", " prrr", " *purrs*", " nyaa~", " mew"]



async def _build_discord_message_content(
    text: str,
    attachments,
) -> types.Content:
    """Convert a Discord message into a multimodal agent payload."""
    normalized_text = (text or "").strip()
    parts: list[types.Part] = []
    attachment_names: list[str] = []

    for attachment in attachments:
        filename = attachment.filename or "attachment"
        attachment_names.append(filename)

        if attachment.size > MAX_INLINE_ATTACHMENT_BYTES:
            raise ValueError(
                f"Attachment '{filename}' is too large ({attachment.size:,} bytes). "
                f"Maximum supported size is {MAX_INLINE_ATTACHMENT_BYTES:,} bytes."
            )

        data = await attachment.read()
        parts.append(
            types.Part(
                inline_data=types.Blob(
                    mime_type=attachment.content_type or FALLBACK_MIME_TYPE,
                    data=data,
                )
            )
        )

    if attachment_names:
        if normalized_text:
            prefix = normalized_text
        else:
            prefix = "The user sent attachment(s) with no accompanying text."
        parts.insert(
            0,
            types.Part(
                text=f"{prefix}\n\nAttachments: {', '.join(attachment_names)}"
            ),
        )
    elif normalized_text:
        parts.append(types.Part(text=normalized_text))

    return types.Content(role="user", parts=parts)


class _ConfirmationView(discord.ui.View):
    """Discord buttons for resolving one pending ADK confirmation."""

    def __init__(
        self,
        gateway: "SchoopetGateway",
        user_id: str,
        pending_id: str,
        session_scope: str = "",
    ):
        super().__init__(timeout=15 * 60)
        self._gateway = gateway
        self._user_id = user_id
        self._pending_id = pending_id
        self._session_scope = session_scope

        approve = discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"{pending_id}:approve",
        )
        reject = discord.ui.Button(
            label="Reject",
            style=discord.ButtonStyle.danger,
            custom_id=f"{pending_id}:reject",
        )
        approve.callback = self._approve
        reject.callback = self._reject
        self.add_item(approve)
        self.add_item(reject)

    async def _approve(self, interaction: discord.Interaction) -> None:
        await self._gateway._resolve_confirmation_button(
            interaction=interaction,
            user_id=self._user_id,
            pending_id=self._pending_id,
            confirmed=True,
            view=self,
            session_scope=self._session_scope,
        )

    async def _reject(self, interaction: discord.Interaction) -> None:
        await self._gateway._resolve_confirmation_button(
            interaction=interaction,
            user_id=self._user_id,
            pending_id=self._pending_id,
            confirmed=False,
            view=self,
            session_scope=self._session_scope,
        )

    def disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True


class SchoopetGateway(discord.Client):
    """Discord gateway client that routes DMs and @mentions to the agent."""

    # Fix 3: bounded set of recently processed message IDs to drop Discord duplicates
    _SEEN_IDS_MAX = 500

    def __init__(self, session_manager, agent_client, rate_limiter=None):
        intents = discord.Intents.none()
        intents.dm_messages = True       # receive DMs (not privileged)
        intents.guild_messages = True    # receive server messages (not privileged)
        intents.message_content = True   # read message text (privileged — must be enabled in portal)
        super().__init__(intents=intents)
        self._session_manager = session_manager
        self._agent_client = agent_client
        self._rate_limiter = rate_limiter
        self._seen_message_ids: set[int] = set()

    async def on_ready(self):
        logger.info(f"Discord gateway connected: {self.user} (id={self.user.id})")

    async def on_message(self, message: discord.Message):
        # Never respond to ourselves
        if message.author == self.user or message.author.bot:
            return

        # Fix 3: drop duplicate deliveries (can occur after WebSocket reconnects)
        if message.id in self._seen_message_ids:
            logger.warning(f"Duplicate message {message.id} ignored")
            return
        if len(self._seen_message_ids) >= self._SEEN_IDS_MAX:
            self._seen_message_ids.pop()
        self._seen_message_ids.add(message.id)

        is_mention = self.user in message.mentions

        # Strip @mention prefix from server messages
        text = message.content
        if is_mention:
            text = (
                text
                .replace(f"<@{self.user.id}>", "")
                .replace(f"<@!{self.user.id}>", "")
                .strip()
            )

        if not text and not message.attachments:
            return

        # Fix 1: build context once and reuse for both logging and the handler
        discord_context = context_from_discord_channel(message.channel)
        user_id = str(message.author.id)
        logger.info(
            f"Discord gateway message: user={user_id} "
            f"scope={discord_context.session_scope} "
            f"len={len(text)} "
            f"attachments={len(message.attachments)}"
        )

        asyncio.create_task(
            self._session_manager.record_discord_channel(
                user_id=user_id,
                channel_id=discord_context.channel_id,
                channel_name=discord_context.channel_name,
                guild_id=discord_context.guild_id,
            )
        )

        # Fix 2: start typing before downloading attachments so the user sees feedback immediately
        async def _keep_typing():
            try:
                async with message.channel.typing():
                    await asyncio.sleep(300)  # context manager keeps refreshing; cancelled when done
            except Exception:
                pass

        typing_task = asyncio.create_task(_keep_typing())
        try:
            content = await _build_discord_message_content(text, message.attachments)
        except ValueError as e:
            typing_task.cancel()
            await message.channel.send(str(e))
            return
        except Exception as e:
            typing_task.cancel()
            logger.exception(f"Failed to read Discord attachments for user {user_id}: {e}")
            await message.channel.send(
                "I couldn't read one of your attachments. Please try again."
            )
            return

        try:
            await self._handle_gateway_message(
                user_id,
                content,
                message.channel,
                discord_context=discord_context,
            )
        finally:
            typing_task.cancel()

    async def _send_channel_text(self, channel, response_text: str) -> None:
        for chunk in _split_message(response_text):
            await channel.send(chunk)

    async def _send_auth_link(self, channel, auth_uri: str, nonce: str, user_id: str) -> None:
        from urllib.parse import urlparse, quote
        from ..config import get_settings
        settings = get_settings()
        continue_uri = settings.IAM_CONNECTOR_CONTINUE_URI
        parsed = urlparse(continue_uri)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        short_url = f"{base_url}/oauth/authorize?nonce={nonce}&uid={quote(user_id, safe='')}"
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Authorize Google Account",
            style=discord.ButtonStyle.link,
            url=short_url,
            emoji="🔗",
        ))
        await channel.send(
            "I need access to your Google account to use this feature. "
            "Click the button below to authorize:",
            view=view,
        )

    async def _store_and_send_confirmations(
        self,
        user_id: str,
        confirmations: list,
        session_id: str,
        channel,
        session_scope: str,
        followup=None,
    ) -> None:
        pending_by_group: dict[str, list[dict]] = {}
        send_group: dict[str, bool] = {}

        for confirmation in confirmations:
            pending = await self._session_manager.add_pending_approval(
                user_id=user_id,
                confirmation=confirmation,
                session_id=session_id,
                channel="discord",
                session_scope=session_scope,
            )
            group_id = pending.get("approval_group_id") or pending["id"]
            pending_by_group.setdefault(group_id, []).append(pending)
            send_group[group_id] = (
                send_group.get(group_id, False)
                or pending.get("is_group_notification_owner", True)
            )

        for group_id, pending_group in pending_by_group.items():
            if not send_group.get(group_id, True):
                continue
            if not self._session_manager.should_send_pending_approval_notification(pending_group):
                continue
            notification_id = self._session_manager.pending_approval_notification_id(pending_group)
            message = self._session_manager.format_pending_approval_notification(pending_group)
            view = _ConfirmationView(
                self,
                user_id,
                notification_id,
                session_scope=session_scope,
            )
            if followup is not None:
                await followup.send(message, view=view)
            else:
                await channel.send(message, view=view)

    async def _handle_gateway_message(
        self,
        user_id: str,
        message: str | types.Content,
        channel,
        discord_context: DiscordContext | None = None,
    ) -> None:
        """Event-aware Discord processing for DMs and guild channels."""
        start_time = time.time()
        discord_context = discord_context or context_from_discord_channel(channel)

        try:
            if self._rate_limiter:
                is_allowed, count = await self._rate_limiter.check_and_increment(user_id)
                if not is_allowed:
                    logger.warning(
                        f"Rate limit exceeded for Discord user {user_id}: {count} messages today"
                    )
                    await self._send_channel_text(channel, RATE_LIMIT_MSG)
                    return

            session_info = await self._session_manager.get_or_create_session(
                user_id,
                channel="discord",
                session_scope=discord_context.session_scope,
                state_extra=discord_context.state_extra,
            )
            logger.info(
                f"Forwarding gateway message to agent for Discord user {user_id}: "
                f"session={session_info.agent_session_id}, "
                f"scope={discord_context.session_scope}, "
                f"is_new_session={session_info.is_new_session}"
            )

            status_msg = None
            try:
                _starters = ["> working..."] + _CAT_STARTERS * 3
                status_msg = await channel.send(random.choice(_starters))

                async def _update_status(tool_name: str) -> None:
                    try:
                        label = _format_tool_name(tool_name)
                        suffix = random.choice(_CAT_SUFFIXES) if random.random() < 2 / 3 else ""
                        await status_msg.edit(content=f"> {label}{suffix}...")
                    except Exception:
                        pass

                try:
                    events = await self._agent_client.send_message_events(
                        user_id=user_id,
                        session_id=session_info.agent_session_id,
                        message=wrap_message_with_discord_context(message, discord_context),
                        on_tool_call=_update_status,
                    )
                except asyncio.TimeoutError:
                    logger.error(f"Agent timeout for Discord gateway user {user_id}", exc_info=True)
                    await self._send_channel_text(
                        channel,
                        "I'm taking longer than usual to respond. Please try again in a moment.",
                    )
                    return

                events, credential_req = await self._agent_client.resolve_iam_credential_events(
                    user_id=user_id,
                    session_id=session_info.agent_session_id,
                    events=events,
                    context="discord",
                )
                if credential_req:
                    await self._session_manager.set_pending_credential(
                        nonce=credential_req.nonce,
                        user_id=user_id,
                        session_id=session_info.agent_session_id,
                        credential_function_call_id=credential_req.function_call_id,
                        auth_config_dict=credential_req.auth_config_dict,
                        auth_uri=credential_req.auth_uri,
                    )
                    await self._send_auth_link(channel, credential_req.auth_uri, credential_req.nonce, user_id)
                    await self._session_manager.update_last_activity(
                        user_id,
                        channel="discord",
                        session_scope=discord_context.session_scope,
                    )
                    return

                confirmations = self._agent_client.extract_confirmation_requests(events)
                if confirmations:
                    uid = user_id if len(user_id) > 4 else user_id
                    tool_names = [c.tool_name for c in confirmations]
                    logger.info(
                        f"[confirm] Agent suspended (online): user={uid} "
                        f"tools={tool_names} count={len(confirmations)} "
                        f"session={session_info.agent_session_id}"
                    )
                    await self._store_and_send_confirmations(
                        user_id=user_id,
                        confirmations=confirmations,
                        session_id=session_info.agent_session_id,
                        channel=channel,
                        session_scope=discord_context.session_scope,
                    )
                    await self._session_manager.update_last_activity(
                        user_id,
                        channel="discord",
                        session_scope=discord_context.session_scope,
                    )
                    return

                response = self._agent_client.extract_text(events)
                if not response:
                    logger.warning(f"Empty response from agent for Discord gateway user {user_id}")
                    await self._send_channel_text(
                        channel, "I couldn't generate a response. Please try again."
                    )
                    return

                await self._send_channel_text(channel, response)
                await self._session_manager.update_last_activity(
                    user_id,
                    channel="discord",
                    session_scope=discord_context.session_scope,
                )

                processing_time = (time.time() - start_time) * 1000
                logger.info(
                    f"Processed Discord gateway message in {processing_time:.0f}ms: "
                    f"response sent to {user_id} ({len(response)} chars)"
                )
            finally:
                if status_msg is not None:
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
        except Exception as e:
            logger.exception(f"Error processing Discord gateway message for {user_id}: {e}")
            await self._send_channel_text(channel, "Something went wrong. Please try again.")

    async def _resolve_confirmation_button(
        self,
        interaction: discord.Interaction,
        user_id: str,
        pending_id: str,
        confirmed: bool,
        view: _ConfirmationView,
        session_scope: str = "",
    ) -> None:
        """Resolve a stored ADK confirmation from a Discord button click."""
        clicking_user_id = str(interaction.user.id)
        if clicking_user_id != user_id:
            await interaction.response.send_message(
                "This approval is not for you.",
                ephemeral=True,
            )
            return

        pending = await self._session_manager.get_pending_approval(
            user_id,
            pending_id,
            session_scope=session_scope or None,
        )
        if not pending:
            view.disable_buttons()
            await interaction.response.edit_message(view=view)
            await interaction.followup.send("That approval is no longer pending.")
            return

        uid = user_id if len(user_id) > 4 else user_id
        tool_name = pending.get("tool_name", "unknown")
        action = "approved" if confirmed else "rejected"
        logger.info(
            f"[confirm] User response (online): user={uid} pending_id={pending_id} "
            f"tool={tool_name} action={action}"
        )

        try:
            pending_group = await self._session_manager.get_pending_approval_group(
                user_id,
                pending_id,
                session_scope=session_scope or None,
            )
            if not pending_group:
                pending_group = [pending]

            for grouped_pending in pending_group:
                logger.info(
                    f"[confirm] Agent resuming (online): user={uid} "
                    f"tool={grouped_pending.get('tool_name', tool_name)} "
                    f"fc_id={grouped_pending.get('adk_confirmation_function_call_id')!r} "
                    f"confirmed={confirmed}"
                )
            all_events = await self._agent_client.send_confirmation_responses_batch(
                user_id=user_id,
                session_id=pending_group[0]["agent_session_id"],
                confirmations=[
                    (p["adk_confirmation_function_call_id"], confirmed)
                    for p in pending_group
                ],
            )

            await self._session_manager.clear_pending_approval_group(
                user_id,
                pending_id,
                session_scope=session_scope or None,
            )
            view.disable_buttons()
            await interaction.response.edit_message(view=view)

            new_confirmations = self._agent_client.extract_confirmation_requests(all_events)
            response = self._agent_client.extract_text(all_events)
            logger.info(
                f"[confirm] Agent resumed (online): user={uid} pending_id={pending_id} "
                f"tool={tool_name} response_chars={len(response)} "
                f"chained_confirmations={len(new_confirmations)}"
            )
            if new_confirmations:
                chained_tools = [c.tool_name for c in new_confirmations]
                logger.info(
                    f"[confirm] Agent suspended (online, chained): user={uid} "
                    f"tools={chained_tools} count={len(new_confirmations)}"
                )
                # send chained confirmations to channel (interaction tokens expire after 15 min)
                await self._store_and_send_confirmations(
                    user_id=user_id,
                    confirmations=new_confirmations,
                    session_id=pending["agent_session_id"],
                    channel=interaction.channel,
                    session_scope=session_scope,
                )
            else:
                if response:
                    for chunk in _split_message(response):
                        await interaction.followup.send(chunk)
            await self._session_manager.update_last_activity(
                user_id,
                channel="discord",
                session_scope=session_scope,
            )
        except Exception as e:
            logger.exception(f"Failed to resolve Discord confirmation for {user_id}: {e}")
            await interaction.response.send_message(
                "I couldn't resolve that approval. Please try again.",
                ephemeral=True,
            )


async def _run_gateway_with_retry(session_manager, agent_client, rate_limiter, bot_token: str) -> None:
    """Run the gateway, retrying with backoff if the connection fails."""
    delay = 30
    while True:
        client = SchoopetGateway(
            session_manager=session_manager,
            agent_client=agent_client,
            rate_limiter=rate_limiter,
        )
        try:
            await client.start(bot_token)
        except Exception as e:
            logger.warning(f"Discord gateway disconnected ({e}), retrying in {delay}s", exc_info=True)
            await client.close()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)  # cap at 5 minutes


async def start_gateway(bot_token: str, session_manager, agent_client, rate_limiter=None) -> None:
    """Create and start the gateway client as a background asyncio task."""
    asyncio.create_task(_run_gateway_with_retry(session_manager, agent_client, rate_limiter, bot_token))
    logger.info("Discord gateway task started")
