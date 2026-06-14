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


@require_owner
async def cmd_delete(client: Client, message: Message):
    """Delete a series, specific quality, or specific episode from the database."""
    args = _parse_args(message)
    if not args:
        await message.reply_text("Usage: /delete <series_slug> [quality] [episode_key]\n\nExamples:\n/delete naruto 1080p\n/delete naruto 1080p S1E01")
        return
        
    series_slug = args[0]
    quality = args[1] if len(args) > 1 else None
    episode_key = args[2] if len(args) > 2 else None
    
    from bot.database import db
    from bot.library import library_manager
    if not db:
        await message.reply_text("⚠️ Database not initialized.")
        return
        
    query = {"series_slug": series_slug}
    if quality:
        query["quality"] = quality
    if episode_key:
        query["episode_key"] = episode_key
        
    # Delete from files collection
    deleted_files = await db.files.delete_many(query)
    
    # Check if we should delete or just update the library message
    messages_deleted = 0
    if episode_key:
        # We only deleted one episode. We shouldn't delete the whole library post!
        # Instead, we just trigger an update for the library post by calling _save_locked?
        # Actually, if we delete the file, the next time someone downloads it will recreate it.
        # But for now, we'll just let the user know they might need to redownload to fix the message.
        await message.reply_text(
            f"🗑️ Deleted {deleted_files.deleted_count} files for <code>{series_slug}</code> {quality or ''} {episode_key}.\n\n"
            f"<i>Note: The channel message will update automatically the next time you download an episode for this series.</i>",
            parse_mode=enums.ParseMode.HTML
        )
        return
        
    # If deleting whole series or whole quality, delete the library message
    cursor = db.library.find(query)
    async for entry in cursor:
        msg_id = entry.get("message_id")
        if msg_id and library_manager and library_manager.channel:
            try:
                await client.delete_messages(library_manager.channel, msg_id)
            except Exception as e:
                log.warning("Could not delete channel message %s: %s", msg_id, e)
        messages_deleted += 1
        
    await db.library.delete_many(query)
    
    if deleted_files.deleted_count == 0:
        await message.reply_text(
            f"⚠️ No files found for <code>{series_slug}</code>.\n\n"
            f"<b>Important:</b> Make sure you are using the exact URL slug with hyphens, not the title!\n"
            f"For example: use <code>yowayowa-sensei</code> instead of <code>Yowayowa Sensei</code>.",
            parse_mode=enums.ParseMode.HTML
        )
        return
        
    await message.reply_text(
        f"🗑️ Deleted {deleted_files.deleted_count} files and {messages_deleted} library posts for <code>{series_slug}</code>{' ' + quality if quality else ''}.",
        parse_mode=enums.ParseMode.HTML
    )
