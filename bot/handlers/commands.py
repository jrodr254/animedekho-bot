"""Slash command handlers."""

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot.keyboards import main_menu


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎌 <b>AnimeDekho Bot</b>\n\n"
        "Stream Hindi / Tamil / Telugu dubbed anime!\n\n"
        "• 🔍 <b>Search</b> — find any anime or movie\n"
        "• 📺 <b>Series</b> — browse recent series\n"
        "• 🎬 <b>Movies</b> — browse movies\n"
        "• 📂 <b>Genres</b> — filter by genre\n\n"
        "Pick an option or just type an anime name to search:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
    )


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Send me the anime name:")
    ctx.user_data["awaiting_search"] = True


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Commands</b>\n\n"
        "/start — Main menu\n"
        "/search — Search anime\n"
        "/help — This message\n\n"
        "Or just type any anime name to search directly!",
        parse_mode=ParseMode.HTML,
    )
