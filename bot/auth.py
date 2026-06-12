"""User authorization — owner-managed approved user list (MongoDB-backed)."""

from __future__ import annotations
import logging
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import settings

log = logging.getLogger(__name__)


# ── Checks ─────────────────────────────────────────────────────────────

def is_owner(user_id: int) -> bool:
    return user_id == settings.bot.owner_id


async def is_approved(user_id: int) -> bool:
    """Check if user is approved (async — uses MongoDB)."""
    if is_owner(user_id):
        return True
    from bot.database import db
    if db is None:
        log.warning("Database not initialized, denying user %d", user_id)
        return False
    return await db.is_approved(user_id)


async def add_user(user_id: int, username: str = "", added_by: int = 0) -> bool:
    """Add user. Returns True if newly added."""
    from bot.database import db
    if db is None:
        return False
    return await db.add_user(user_id, username, added_by)


async def remove_user(user_id: int) -> bool:
    """Remove user. Returns True if removed."""
    from bot.database import db
    if db is None:
        return False
    return await db.remove_user(user_id)


async def get_users() -> list[int]:
    """Get all approved user IDs."""
    from bot.database import db
    if db is None:
        return []
    return await db.get_users()


# ── Decorator ──────────────────────────────────────────────────────────

def require_approved(func):
    """Decorator: blocks unapproved users. Checks force sub too."""
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if not await is_approved(user_id):
            target = update.callback_query or update.message
            if target:
                text = "🔒 You don't have access. Ask the owner to add you."
                if hasattr(target, "answer"):
                    await target.answer(text, show_alert=True)
                else:
                    await target.reply_text(text)
            return

        # Force sub check (owner bypasses)
        if not is_owner(user_id):
            from bot.forcesub import check_subscription
            from bot.database import db

            channel_id = settings.bot.main_channel
            if channel_id and not await check_subscription(ctx.bot, user_id, channel_id):
                # Get channel invite link
                invite_link = None
                if db:
                    invite_link = await db.get_config("channel_invite_link")

                target = update.callback_query or update.message
                if target:
                    text = "📢 You must join our channel to use this bot!"
                    if invite_link:
                        markup = InlineKeyboardMarkup([[
                            InlineKeyboardButton("Join Channel", url=invite_link),
                        ]])
                    else:
                        markup = None
                        text += "\n\nPlease contact the owner for the channel link."

                    if hasattr(target, "answer"):
                        await target.answer(text, show_alert=True)
                        if update.callback_query and update.callback_query.message:
                            await update.callback_query.message.reply_text(
                                text, reply_markup=markup,
                            )
                    else:
                        await target.reply_text(text, reply_markup=markup)
                return

        return await func(update, ctx)
    return wrapper


def require_owner(func):
    """Decorator: blocks non-owners."""
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if not is_owner(user_id):
            if update.message:
                await update.message.reply_text("⚠️ This command is owner-only.")
            return
        return await func(update, ctx)
    return wrapper
