"""Owner-only admin commands."""

from pyrogram import Client, enums
from pyrogram.types import Message

from bot.auth import require_owner, add_user, remove_user, get_users, is_owner
import bot.logger


def _parse_args(message: Message) -> list[str]:
    """Parse arguments from message text (everything after the command)."""
    parts = message.text.split()
    return parts[1:] if len(parts) > 1 else []


@require_owner
async def cmd_adduser(client: Client, message: Message):
    args = _parse_args(message)
    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text("Usage: /adduser <telegram_id>")
        return
    uid = int(args[0])
    if is_owner(uid):
        await message.reply_text("👑 That's the owner — already has access.")
        return
    if await add_user(uid, added_by=message.from_user.id):
        await message.reply_text(f"✅ User <code>{uid}</code> added.", parse_mode=enums.ParseMode.HTML)
        if bot.logger.bot_logger:
            await bot.logger.bot_logger.log_user_added(uid)
    else:
        await message.reply_text("ℹ️ User already approved.")


@require_owner
async def cmd_removeuser(client: Client, message: Message):
    args = _parse_args(message)
    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text("Usage: /removeuser <telegram_id>")
        return
    uid = int(args[0])
    if is_owner(uid):
        await message.reply_text("👑 Can't remove the owner.")
        return
    if await remove_user(uid):
        await message.reply_text(f"✅ User <code>{uid}</code> removed.", parse_mode=enums.ParseMode.HTML)
        if bot.logger.bot_logger:
            await bot.logger.bot_logger.log_user_removed(uid)
    else:
        await message.reply_text("ℹ️ User not in the approved list.")


@require_owner
async def cmd_users(client: Client, message: Message):
    users = await get_users()
    if not users:
        await message.reply_text("📋 No approved users (only owner has access).")
        return
    lines = [f"  • <code>{uid}</code>" for uid in users]
    await message.reply_text(
        f"📋 <b>Approved Users ({len(users)}):</b>\n" + "\n".join(lines),
        parse_mode=enums.ParseMode.HTML,
    )


@require_owner
async def cmd_setlogchannel(client: Client, message: Message):
    args = _parse_args(message)
    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text("Usage: /setlogchannel <channel_id>\n\nTip: forward a message from the channel to @userinfobot to get the ID.")
        return
    cid = int(args[0])
    if bot.logger.bot_logger:
        # Test sending a message to the channel
        try:
            await client.send_message(cid, "🔗 Log channel connected!")
            await message.reply_text(f"✅ Log channel set to <code>{cid}</code>\nTest message sent successfully!", parse_mode=enums.ParseMode.HTML)
            bot.logger.bot_logger.set_log_channel(cid)
        except Exception as e:
            await message.reply_text(f"❌ Failed to send message to log channel <code>{cid}</code>.\nError: <code>{e}</code>\n\nMake sure the bot is an admin with 'Send Messages' permission and the ID starts with -100.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text("⚠️ Logger not initialized yet.")


@require_owner
async def cmd_setmainchannel(client: Client, message: Message):
    args = _parse_args(message)
    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text("Usage: /setmainchannel <channel_id>")
        return
    cid = int(args[0])
    if bot.logger.bot_logger:
        bot.logger.bot_logger.set_main_channel(cid)
        from bot.library import library_manager
        if library_manager:
            library_manager.channel = cid
        
        # Test sending a message to the channel
        try:
            await client.send_message(cid, "🔗 Main channel connected!")
            await message.reply_text(f"✅ Main channel set to <code>{cid}</code>\nTest message sent successfully!", parse_mode=enums.ParseMode.HTML)
            bot.logger.bot_logger.set_main_channel(cid)
        except Exception as e:
            await message.reply_text(f"❌ Failed to send message to main channel <code>{cid}</code>.\nError: <code>{e}</code>\n\nMake sure the bot is an admin with 'Send Messages' permission and the ID starts with -100.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text("⚠️ Logger not initialized yet.")


@require_owner
async def cmd_setchannellink(client: Client, message: Message):
    """Set the channel invite link for force subscribe button."""
    args = _parse_args(message)
    if not args:
        await message.reply_text(
            "Usage: /setchannellink <invite_link>\n\n"
            "Example: /setchannellink https://t.me/yourchannel"
        )
        return
    link = args[0].strip()
    if not link.startswith("https://"):
        await message.reply_text("⚠️ Please provide a valid HTTPS link.")
        return

    from bot.database import db
    if db:
        await db.set_config("channel_invite_link", link)
        await message.reply_text(
            f"✅ Channel invite link set:\n<code>{link}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text("⚠️ Database not initialized yet.")
