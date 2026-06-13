"""Force subscribe — ensures users have joined the main channel."""

from __future__ import annotations
import logging

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus

log = logging.getLogger(__name__)


async def check_subscription(client: Client, user_id: int, channel_id: int) -> bool:
    """Check if user is a member of the channel."""
    if not channel_id:
        return True  # No channel configured, skip check
    try:
        member = await client.get_chat_member(channel_id, user_id)
        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception as e:
        log.debug("Force sub check failed for %d: %s", user_id, e)
        return False
