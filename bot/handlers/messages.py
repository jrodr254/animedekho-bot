"""Text message handler — treats any text as a search query."""

import logging

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from api.client import api
from bot.keyboards import search_results
from utils.helpers import esc

log = logging.getLogger(__name__)


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query:
        return

    ctx.user_data["awaiting_search"] = False
    msg = await update.message.reply_text("🔍 Searching...")

    try:
        results = await api.search(query)
        if not results:
            await msg.edit_text("❌ No results found. Try a different name.")
            return

        await msg.edit_text(
            f"🔍 <b>Results for:</b> {esc(query)}\n\nSelect one:",
            parse_mode=ParseMode.HTML,
            reply_markup=search_results(results),
        )
    except Exception as e:
        log.exception("Search failed")
        await msg.edit_text(f"⚠️ Search error: {esc(str(e)[:150])}")
