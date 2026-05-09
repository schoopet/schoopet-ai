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
import json
import logging
import time

import discord
from google.genai import types

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
MAX_CONFIRMATION_ARG_CHARS = 700


def _summarize_confirmation_args(args: dict) -> str:
    """Return a compact, stable summary for a confirmation prompt."""
    if not args:
        return ""
    try:
        text = json.dumps(args, sort_keys=True, default=str)
    except TypeError:
        text = str(args)
    if len(text) > MAX_CONFIRMATION_ARG_CHARS:
        return f"{text[:MAX_CONFIRMATION_ARG_CHARS - 3]}..."
    return text


def _format_confirmation_prompt(confirmation) -> str:
    args = _summarize_confirmation_args(confirmation.tool_args)
    return f"Approve this action?\n{confirmation.tool_name}({args})"


async def _get_pending_confirmation(session_manager, user_id: str, pending_id: str, session_scope: str = ""):
    if session_scope:
        return await session_manager.get_pending_confirmation(
            user_id,
            pending_id,
            session_scope=session_scope,
        )
    return await session_manager.get_pending_confirmation(user_id, pending_id)


async def _clear_pending_confirmation(session_manager, user_id: str, pending_id: str, session_scope: str = ""):
    if session_scope:
        await session_manager.clear_pending_confirmation(
            user_id,
            pending_id,
            session_scope=session_scope,
        )
        return
    await session_manager.clear_pending_confirmation(user_id, pending_id)


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

    def __init__(self, session_manager, agent_client, rate_limiter=None):
        intents = discord.Intents.none()
        intents.dm_messages = True       # receive DMs (not privileged)
        intents.guild_messages = True    # receive server messages (not privileged)
        intents.message_content = True   # read message text (privileged — must be enabled in portal)
        super().__init__(intents=intents)
        self._session_manager = session_manager
        self._agent_client = agent_client
        self._rate_limiter = rate_limiter

    async def on_ready(self):
        logger.info(f"Discord gateway connected: {self.user} (id={self.user.id})")

    async def on_message(self, message: discord.Message):
        # Never respond to ourselves
        if message.author == self.user or message.author.bot:
            return

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

        user_id = str(message.author.id)
        logger.info(
            f"Discord gateway message: user={user_id} "
            f"scope={context_from_discord_channel(message.channel).session_scope} "
            f"len={len(text)} "
            f"attachments={len(message.attachments)}"
        )

        try:
            content = await _build_discord_message_content(text, message.attachments)
        except ValueError as e:
            await message.channel.send(str(e))
            return
        except Exception as e:
            logger.exception(f"Failed to read Discord attachments for user {user_id}: {e}")
            await message.channel.send(
                "I couldn't read one of your attachments. Please try again."
            )
            return

        # Fire typing indicator as a background task so it never blocks message processing.
        async def _keep_typing():
            try:
                async with message.channel.typing():
                    await asyncio.sleep(300)  # context manager keeps refreshing; cancelled when done
            except Exception:
                pass

        typing_task = asyncio.create_task(_keep_typing())
        try:
            await self._handle_gateway_message(
                user_id,
                content,
                message.channel,
                discord_context=context_from_discord_channel(message.channel),
            )
        finally:
            typing_task.cancel()

    async def _send_channel_text(self, channel, response_text: str) -> None:
        for chunk in _split_message(response_text):
            await channel.send(chunk)

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

            try:
                events = await self._agent_client.send_message_events(
                    user_id=user_id,
                    session_id=session_info.agent_session_id,
                    message=wrap_message_with_discord_context(message, discord_context),
                )
            except asyncio.TimeoutError:
                logger.error(f"Agent timeout for Discord gateway user {user_id}")
                await self._send_channel_text(
                    channel,
                    "I'm taking longer than usual to respond. Please try again in a moment.",
                )
                return

            confirmations = self._agent_client.extract_confirmation_requests(events)
            if confirmations:
                for confirmation in confirmations:
                    pending = await self._session_manager.set_pending_confirmation(
                        user_id=user_id,
                        confirmation=confirmation,
                        session_id=session_info.agent_session_id,
                        channel="discord",
                        session_scope=discord_context.session_scope,
                    )
                    await channel.send(
                        _format_confirmation_prompt(confirmation),
                        view=_ConfirmationView(
                            self,
                            user_id,
                            pending["id"],
                            session_scope=discord_context.session_scope,
                        ),
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

        pending = await _get_pending_confirmation(
            self._session_manager,
            user_id,
            pending_id,
            session_scope=session_scope,
        )
        if not pending:
            view.disable_buttons()
            await interaction.response.edit_message(view=view)
            await interaction.followup.send("That approval is no longer pending.")
            return

        try:
            events = await self._agent_client.send_confirmation_response(
                user_id=user_id,
                session_id=pending["agent_session_id"],
                confirmation_function_call_id=pending["adk_confirmation_function_call_id"],
                confirmed=confirmed,
            )
            await _clear_pending_confirmation(
                self._session_manager,
                user_id,
                pending_id,
                session_scope=session_scope,
            )
            view.disable_buttons()
            await interaction.response.edit_message(view=view)

            new_confirmations = self._agent_client.extract_confirmation_requests(events)
            if new_confirmations:
                for conf in new_confirmations:
                    new_pending = await self._session_manager.set_pending_confirmation(
                        user_id=user_id,
                        confirmation=conf,
                        session_id=pending["agent_session_id"],
                        channel="discord",
                        session_scope=session_scope,
                    )
                    await interaction.followup.send(
                        _format_confirmation_prompt(conf),
                        view=_ConfirmationView(
                            self,
                            user_id,
                            new_pending["id"],
                            session_scope=session_scope,
                        ),
                    )
            else:
                response = self._agent_client.extract_text(events)
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


async def _run_gateway_with_retry(client: "SchoopetGateway", bot_token: str) -> None:
    """Run the gateway, retrying with backoff if the connection fails."""
    delay = 30
    while True:
        try:
            await client.start(bot_token)
        except Exception as e:
            logger.warning(f"Discord gateway disconnected ({e}), retrying in {delay}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)  # cap at 5 minutes


async def start_gateway(bot_token: str, session_manager, agent_client, rate_limiter=None) -> SchoopetGateway:
    """Create and start the gateway client as a background asyncio task.

    Returns the client so the caller can close it on shutdown.
    """
    client = SchoopetGateway(
        session_manager=session_manager,
        agent_client=agent_client,
        rate_limiter=rate_limiter,
    )
    asyncio.create_task(_run_gateway_with_retry(client, bot_token))
    logger.info("Discord gateway task started")
    return client
