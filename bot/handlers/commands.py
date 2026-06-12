"""Slash command handlers."""

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot.keyboards import main_menu
from bot.auth import require_approved
from bot.logger import bot_logger


@require_approved
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if bot_logger:
        await bot_logger.log_bot_start(user.id, user.username or user.first_name)

    await update.message.reply_text(
        "🎌 <b>AnimeDekho Bot</b>\n\n"
        "Stream Hindi / Tamil / Telugu dubbed anime!\n\n"
        "• 📺 <b>Series</b> — browse recent series\n"
        "• 🎬 <b>Movies</b> — browse movies\n"
        "• 📂 <b>Genres</b> — filter by genre\n\n"
        "Just type any anime name to search!",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
    )


@require_approved
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Commands</b>\n\n"
        "/start — Main menu\n"
        "/help — This message\n\n"
        "Just type any anime name to search!\n\n"
        "<b>Owner Commands:</b>\n"
        "/adduser &lt;id&gt; — Approve a user\n"
        "/removeuser &lt;id&gt; — Remove a user\n"
        "/users — List approved users\n"
        "/setlogchannel &lt;id&gt; — Set log channel\n"
        "/setmainchannel &lt;id&gt; — Set main channel",
        parse_mode=ParseMode.HTML,
    )
