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

import discord

from .handler import _handle_discord_message

logger = logging.getLogger(__name__)


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

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mention = self.user in message.mentions

        if not is_dm and not is_mention:
            return

        # Strip @mention prefix from server messages
        text = message.content
        if is_mention:
            text = (
                text
                .replace(f"<@{self.user.id}>", "")
                .replace(f"<@!{self.user.id}>", "")
                .strip()
            )

        if not text:
            return

        user_id = str(message.author.id)
        logger.info(
            f"Discord gateway message: user={user_id} "
            f"source={'DM' if is_dm else 'mention'} len={len(text)}"
        )

        # Show typing indicator while the agent thinks
        async with message.channel.typing():
            async def reply_fn(response_text: str) -> None:
                from .sender import _split_message
                for chunk in _split_message(response_text):
                    await message.channel.send(chunk)

            await _handle_discord_message(user_id, text, reply_fn)


async def start_gateway(bot_token: str, session_manager, agent_client, rate_limiter=None) -> SchoopetGateway:
    """Create and start the gateway client as a background asyncio task.

    Returns the client so the caller can close it on shutdown.
    """
    client = SchoopetGateway(
        session_manager=session_manager,
        agent_client=agent_client,
        rate_limiter=rate_limiter,
    )
    asyncio.create_task(client.start(bot_token))
    logger.info("Discord gateway task started")
    return client
