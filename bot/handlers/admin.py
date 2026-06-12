"""Owner-only admin commands."""

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot.auth import require_owner, add_user, remove_user, get_users, is_owner
from bot.logger import bot_logger


@require_owner
async def cmd_adduser(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or not ctx.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /adduser <telegram_id>")
        return
    uid = int(ctx.args[0])
    if is_owner(uid):
        await update.message.reply_text("👑 That's the owner — already has access.")
        return
    if await add_user(uid, added_by=update.effective_user.id):
        await update.message.reply_text(f"✅ User <code>{uid}</code> added.", parse_mode=ParseMode.HTML)
        if bot_logger:
            await bot_logger.log_user_added(uid)
    else:
        await update.message.reply_text("ℹ️ User already approved.")


@require_owner
async def cmd_removeuser(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or not ctx.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /removeuser <telegram_id>")
        return
    uid = int(ctx.args[0])
    if is_owner(uid):
        await update.message.reply_text("👑 Can't remove the owner.")
        return
    if await remove_user(uid):
        await update.message.reply_text(f"✅ User <code>{uid}</code> removed.", parse_mode=ParseMode.HTML)
        if bot_logger:
            await bot_logger.log_user_removed(uid)
    else:
        await update.message.reply_text("ℹ️ User not in the approved list.")


@require_owner
async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users = await get_users()
    if not users:
        await update.message.reply_text("📋 No approved users (only owner has access).")
        return
    lines = [f"  • <code>{uid}</code>" for uid in users]
    await update.message.reply_text(
        f"📋 <b>Approved Users ({len(users)}):</b>\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


@require_owner
async def cmd_setlogchannel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or not ctx.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /setlogchannel <channel_id>\n\nTip: forward a message from the channel to @userinfobot to get the ID.")
        return
    cid = int(ctx.args[0])
    if bot_logger:
        bot_logger.set_log_channel(cid)
        await update.message.reply_text(f"✅ Log channel set to <code>{cid}</code>", parse_mode=ParseMode.HTML)
        await bot_logger._send_log("🔗 Log channel connected!")
    else:
        await update.message.reply_text("⚠️ Logger not initialized yet.")


@require_owner
async def cmd_setmainchannel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or not ctx.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /setmainchannel <channel_id>")
        return
    cid = int(ctx.args[0])
    if bot_logger:
        bot_logger.set_main_channel(cid)
        await update.message.reply_text(f"✅ Main channel set to <code>{cid}</code>", parse_mode=ParseMode.HTML)
        await bot_logger._send_main("🔗 Main channel connected!")
    else:
        await update.message.reply_text("⚠️ Logger not initialized yet.")


@require_owner
async def cmd_setchannellink(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Set the channel invite link for force subscribe button."""
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /setchannellink <invite_link>\n\n"
            "Example: /setchannellink https://t.me/yourchannel"
        )
        return
    link = ctx.args[0].strip()
    if not link.startswith("https://"):
        await update.message.reply_text("⚠️ Please provide a valid HTTPS link.")
        return

    from bot.database import db
    if db:
        await db.set_config("channel_invite_link", link)
        await update.message.reply_text(
            f"✅ Channel invite link set:\n<code>{link}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("⚠️ Database not initialized yet.")
