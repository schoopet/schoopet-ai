"""Discord session scope and agent message context helpers."""
from dataclasses import dataclass

from google.genai import types


@dataclass(frozen=True)
class DiscordContext:
    """Stable routing/context metadata for one Discord conversation surface."""

    session_scope: str
    channel_id: str
    guild_id: str = ""
    channel_name: str = ""

    @property
    def state_extra(self) -> dict:
        data = {
            "discord_channel_id": self.channel_id,
        }
        if self.guild_id:
            data["discord_guild_id"] = self.guild_id
        if self.channel_name:
            data["discord_channel_name"] = self.channel_name
        return data

    def prefix(self) -> str:
        lines = [
            "Discord context:",
            f"session_scope: {self.session_scope}",
        ]
        if self.guild_id:
            lines.append(f"guild_id: {self.guild_id}")
        lines.append(f"channel_id: {self.channel_id}")
        if self.channel_name:
            lines.append(f"channel_name: {self.channel_name}")
        return "\n".join(lines)


def build_discord_context(
    channel_id: str,
    guild_id: str = "",
    channel_name: str = "",
) -> DiscordContext:
    """Build the session scope for a Discord channel or DM."""
    if guild_id and channel_id:
        session_scope = f"discord:guild:{guild_id}:channel:{channel_id}"
    elif channel_id:
        session_scope = f"discord:dm:{channel_id}"
    else:
        session_scope = ""
    return DiscordContext(
        session_scope=session_scope,
        guild_id=guild_id,
        channel_id=channel_id,
        channel_name=channel_name,
    )


def context_from_discord_channel(channel) -> DiscordContext:
    """Build context metadata from a discord.py channel object."""
    channel_id = str(getattr(channel, "id", ""))
    guild = getattr(channel, "guild", None)
    guild_id = str(getattr(guild, "id", "") or "")
    channel_name = str(getattr(channel, "name", "") or "")
    return build_discord_context(
        channel_id=channel_id,
        guild_id=guild_id,
        channel_name=channel_name,
    )


def wrap_message_with_discord_context(
    message: str | types.Content,
    context: DiscordContext,
) -> str | types.Content:
    """Inject Discord channel identity into the message seen by the agent."""
    prefix = context.prefix()
    if isinstance(message, str):
        return f"{prefix}\n\nUser message:\n{message}"

    parts = list(message.parts or [])
    text_prefix = f"{prefix}\n\nUser message:"
    if parts and getattr(parts[0], "text", None):
        parts[0].text = f"{text_prefix}\n{parts[0].text}"
    else:
        parts.insert(0, types.Part(text=text_prefix))
    return types.Content(role=message.role or "user", parts=parts)
