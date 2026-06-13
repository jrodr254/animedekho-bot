"""Text message handler — treats any text as a search query."""

import logging

from pyrogram import Client, enums
from pyrogram.types import Message

from api.client import api
from bot.keyboards import search_results
from bot.auth import require_approved
from bot.logger import bot_logger
from utils.helpers import esc

log = logging.getLogger(__name__)


@require_approved
async def handle_text(client: Client, message: Message):
    query = message.text.strip()
    if not query:
        return

    user = message.from_user
    msg = await message.reply_text("🔍 Searching...")

    # Log the search
    if bot_logger:
        await bot_logger.log_search(user.id, user.username or user.first_name, query)

    try:
        results = await api.search(query)
        if not results:
            await msg.edit_text("❌ No results found. Try a different name.")
            return

        await msg.edit_text(
            f"🔍 <b>Results for:</b> {esc(query)}\n\nSelect one:",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=search_results(results),
        )
    except Exception as e:
        log.exception("Search failed")
        await msg.edit_text(f"⚠️ Search error: {esc(str(e)[:150])}")
        if bot_logger:
            await bot_logger.log_error("search", str(e))
