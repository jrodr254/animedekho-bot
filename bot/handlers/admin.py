"""Owner-only admin commands."""

import logging
from pyrogram import Client, enums
from pyrogram.types import Message

from bot.auth import require_owner, add_user, remove_user, get_users, is_owner
import bot.logger

log = logging.getLogger(__name__)


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
    """Interactive delete — shows all downloaded series as buttons."""
    from bot.database import db
    if not db:
        await message.reply_text("⚠️ Database not initialized.")
        return

    # Get all unique series from files collection
    pipeline = [
        {"$group": {
            "_id": "$series_slug",
            "title": {"$first": "$series_title"},
            "count": {"$sum": 1},
        }},
        {"$sort": {"title": 1}},
    ]
    series_list = await db.files.aggregate(pipeline).to_list(length=100)

    if not series_list:
        await message.reply_text("📂 No downloaded files in the library.")
        return

    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from utils.helpers import slug_to_title

    buttons = []
    for s in series_list:
        slug = s["_id"]
        title = s.get("title") or slug_to_title(slug)
        count = s["count"]
        buttons.append([InlineKeyboardButton(
            f"📺 {title} ({count} files)",
            callback_data=f"del:s:{slug[:40]}",
        )])

    await message.reply_text(
        "🗑️ <b>Delete Manager</b>\n\nSelect a series to manage:",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def delete_callback(client: Client, query):
    """Handle all delete-related callbacks (del:*)."""
    from bot.database import db
    from bot.library import library_manager
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from utils.helpers import slug_to_title
    from pyrogram import enums as pe

    if not db:
        await query.answer("DB not ready", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data.startswith("del:s:"):
        # Show episodes for this series
        series_slug = data[6:]
        cursor = db.files.find({"series_slug": series_slug})
        all_files = await cursor.to_list(length=None)

        if not all_files:
            await query.edit_message_text("⚠️ No files found for this series.")
            return

        title = all_files[0].get("series_title") or slug_to_title(series_slug)

        # Group by episode
        episodes: dict[str, list[str]] = {}
        for f in all_files:
            ep = f["episode_key"]
            q = f["quality"]
            episodes.setdefault(ep, []).append(q)

        # Sort episodes
        import re
        def _sort_key(k):
            m = re.match(r"S(\d+)E(\d+)", k, re.IGNORECASE)
            return (int(m.group(1)), int(m.group(2))) if m else (999, 0)

        sorted_eps = sorted(episodes.keys(), key=_sort_key)

        buttons = []
        for ep in sorted_eps:
            quals = ", ".join(sorted(episodes[ep]))
            label = f"🎬 {ep} [{quals}]" if ep.lower() == "movie" else f"▶️ {ep} [{quals}]"
            buttons.append([InlineKeyboardButton(
                label,
                callback_data=f"del:e:{series_slug[:30]}:{ep}",
            )])

        # Add "Delete ALL" button
        buttons.append([InlineKeyboardButton(
            f"🗑️ DELETE ENTIRE SERIES ({len(all_files)} files)",
            callback_data=f"del:all:{series_slug[:40]}",
        )])
        # Back button
        buttons.append([InlineKeyboardButton("◀️ Back", callback_data="del:back")])

        await query.edit_message_text(
            f"🗑️ <b>{title}</b>\n\n"
            f"📂 {len(sorted_eps)} episode(s) · {len(all_files)} file(s)\n\n"
            f"Tap an episode to delete it:",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("del:e:"):
        # Show confirmation for deleting a specific episode
        parts = data[6:].rsplit(":", 1)
        series_slug = parts[0]
        episode_key = parts[1]

        # Get qualities for this episode
        cursor = db.files.find({"series_slug": series_slug, "episode_key": episode_key})
        files = await cursor.to_list(length=None)
        title = files[0].get("series_title", series_slug) if files else series_slug
        quals = [f["quality"] for f in files]

        buttons = []
        # Delete specific quality
        for q in sorted(quals):
            buttons.append([InlineKeyboardButton(
                f"🗑️ Delete {episode_key} [{q}]",
                callback_data=f"del:x:{series_slug[:25]}:{episode_key}:{q}",
            )])
        # Delete all qualities for this episode
        if len(quals) > 1:
            buttons.append([InlineKeyboardButton(
                f"🗑️ Delete {episode_key} [ALL qualities]",
                callback_data=f"del:x:{series_slug[:25]}:{episode_key}:*",
            )])
        buttons.append([InlineKeyboardButton("◀️ Back", callback_data=f"del:s:{series_slug[:40]}")])

        await query.edit_message_text(
            f"🗑️ <b>Delete {episode_key}</b>\n"
            f"📺 {slug_to_title(series_slug)}\n"
            f"📊 Qualities: {', '.join(sorted(quals))}\n\n"
            f"What do you want to delete?",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("del:x:"):
        # Execute deletion of specific episode+quality
        parts = data[6:].rsplit(":", 2)
        series_slug = parts[0]
        episode_key = parts[1]
        quality = parts[2]  # "*" means all qualities

        file_query = {"series_slug": series_slug, "episode_key": episode_key}
        if quality != "*":
            file_query["quality"] = quality

        deleted = await db.files.delete_many(file_query)
        await _refresh_album(db, library_manager, series_slug)

        await query.edit_message_text(
            f"✅ <b>Deleted!</b>\n"
            f"🗑️ {deleted.deleted_count} file(s) removed\n"
            f"📺 {episode_key} {'[' + quality + ']' if quality != '*' else '[all qualities]'}",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Back to series", callback_data=f"del:s:{series_slug[:40]}")],
                [InlineKeyboardButton("🏠 Delete menu", callback_data="del:back")],
            ]),
        )

    elif data.startswith("del:all:"):
        # Confirm delete entire series
        series_slug = data[8:]
        count = await db.files.count_documents({"series_slug": series_slug})
        title = slug_to_title(series_slug)

        buttons = [
            [InlineKeyboardButton(
                f"⚠️ YES, DELETE ALL {count} FILES",
                callback_data=f"del:confirm:{series_slug[:40]}",
            )],
            [InlineKeyboardButton("◀️ Cancel", callback_data=f"del:s:{series_slug[:40]}")],
        ]

        await query.edit_message_text(
            f"⚠️ <b>Are you sure?</b>\n\n"
            f"This will permanently delete:\n"
            f"📺 {title}\n"
            f"📁 {count} file(s)\n"
            f"📨 Album post from channel\n\n"
            f"<b>This cannot be undone!</b>",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif data.startswith("del:confirm:"):
        # Execute full series deletion
        series_slug = data[12:]
        deleted = await db.files.delete_many({"series_slug": series_slug})

        if library_manager:
            await library_manager.delete_album(series_slug)

        await db.downloads.delete_many({"series_slug": series_slug})

        await query.edit_message_text(
            f"✅ <b>Series deleted permanently!</b>\n"
            f"🗑️ {deleted.deleted_count} file(s) removed\n"
            f"📨 Album removed from channel",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Delete menu", callback_data="del:back")],
            ]),
        )

    elif data == "del:back":
        # Back to series list — re-run the list
        pipeline = [
            {"$group": {
                "_id": "$series_slug",
                "title": {"$first": "$series_title"},
                "count": {"$sum": 1},
            }},
            {"$sort": {"title": 1}},
        ]
        series_list = await db.files.aggregate(pipeline).to_list(length=100)

        if not series_list:
            await query.edit_message_text("📂 Library is empty — nothing to delete.")
            return

        buttons = []
        for s in series_list:
            slug = s["_id"]
            title = s.get("title") or slug_to_title(slug)
            count = s["count"]
            buttons.append([InlineKeyboardButton(
                f"📺 {title} ({count} files)",
                callback_data=f"del:s:{slug[:40]}",
            )])

        await query.edit_message_text(
            "🗑️ <b>Delete Manager</b>\n\nSelect a series to manage:",
            parse_mode=pe.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def _refresh_album(db, library_manager, series_slug: str):
    """After deletion, update or remove the album in main channel."""
    remaining = await db.files.count_documents({"series_slug": series_slug})
    if remaining == 0:
        if library_manager:
            await library_manager.delete_album(series_slug)
    else:
        if library_manager:
            sample = await db.files.find_one({"series_slug": series_slug})
            if sample:
                await library_manager.save_to_library(
                    series_slug=series_slug,
                    series_title=sample.get("series_title", series_slug),
                    quality=sample["quality"],
                    episode_key=sample["episode_key"],
                    file_id=sample["file_id"],
                    file_unique_id=sample["file_unique_id"],
                )
