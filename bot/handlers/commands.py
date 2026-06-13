"""Slash command handlers."""

import logging
import re

from pyrogram import Client, enums
from pyrogram.types import Message

from bot.keyboards import main_menu
from bot.auth import require_approved
from bot.logger import bot_logger

log = logging.getLogger(__name__)


@require_approved
async def cmd_start(client: Client, message: Message):
    user = message.from_user

    # Check for deep link parameters (file requests from library)
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("get_"):
        await _handle_file_request(client, message, args[1])
        return

    if bot_logger:
        await bot_logger.log_bot_start(user.id, user.username or user.first_name)

    await message.reply_text(
        "🎌 <b>AnimeDekho Bot</b>\n\n"
        "Stream Hindi / Tamil / Telugu dubbed anime!\n\n"
        "• 📺 <b>Series</b> — browse recent series\n"
        "• 🎬 <b>Movies</b> — browse movies\n"
        "• 📂 <b>Genres</b> — filter by genre\n\n"
        "Just type any anime name to search!",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=main_menu(),
    )


@require_approved
async def cmd_help(client: Client, message: Message):
    await message.reply_text(
        "📖 <b>Commands</b>\n\n"
        "/start — Main menu\n"
        "/help — This message\n\n"
        "Just type any anime name to search!\n\n"
        "<b>Owner Commands:</b>\n"
        "/adduser &lt;id&gt; — Approve a user\n"
        "/removeuser &lt;id&gt; — Remove a user\n"
        "/users — List approved users\n"
        "/setlogchannel &lt;id&gt; — Set log channel\n"
        "/setmainchannel &lt;id&gt; — Set main channel\n"
        "/setchannellink &lt;url&gt; — Set channel invite link",
        parse_mode=enums.ParseMode.HTML,
    )


async def _handle_file_request(client: Client, message: Message, param: str):
    """Handle deep link file requests from main channel.

    Format: get_<slug>_<quality>_<episode_key>
    Example: get_naruto-shippuden_720p_S1E01
    """
    from bot.library import library_manager
    from bot.database import db

    if not library_manager:
        await message.reply_text("⚠️ Library not initialized.")
        return

    # Parse: get_<slug>_<quality>_<ep_key>
    raw = param[4:]  # strip "get_"

    # Try to match episode key at the end (S\d+E\d+ or "movie")
    m = re.match(r"^(.+)_(\d+p|auto)_(S\d+E\d+|movie)$", raw, re.IGNORECASE)
    if not m:
        await message.reply_text("⚠️ Invalid file link format.")
        return

    series_slug = m.group(1)
    quality = m.group(2)
    episode_key = m.group(3)

    # Look up file
    file_id = await library_manager.get_file(series_slug, quality, episode_key)
    if not file_id:
        await message.reply_text(
            "❌ File not found in library. It may have been removed or not yet uploaded."
        )
        return

    # Send the file
    try:
        import html as htmlmod
        from utils.helpers import slug_to_title
        title = slug_to_title(series_slug)
        caption = f"📺 {htmlmod.escape(title)} [{quality}]"
        if episode_key.lower() != "movie":
            caption += f" — {episode_key}"

        await message.reply_video(
            video=file_id,
            caption=caption,
            parse_mode=enums.ParseMode.HTML,
        )

        # Log download
        if db:
            user = message.from_user
            await db.log_download(
                user_id=user.id,
                series_slug=series_slug,
                episode=episode_key,
                quality=quality,
                file_id=file_id,
            )
    except Exception as e:
        log.error("Failed to send library file: %s", e)
        # Try as document fallback
        try:
            await message.reply_document(
                document=file_id,
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as e2:
            log.error("Document fallback also failed: %s", e2)
            await message.reply_text(
                "❌ Could not send file. It may have expired from Telegram servers."
            )
