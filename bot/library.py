"""Main Channel Library System — manages anime file catalog in Telegram channel."""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone

from pyrogram import Client, enums

from bot.database import Database

log = logging.getLogger(__name__)

# Telegram caption limit
CAPTION_LIMIT = 1024
# Reserve some chars for header/footer
CAPTION_RESERVE = 150

# Per-series lock to prevent race conditions on concurrent edits
_locks: dict[str, asyncio.Lock] = {}


def _get_lock(key: str) -> asyncio.Lock:
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    # Prune locks if too many
    if len(_locks) > 500:
        oldest = list(_locks.keys())[:200]
        for k in oldest:
            if not _locks[k].locked():
                _locks.pop(k, None)
    return _locks[key]


class LibraryManager:
    def __init__(self, client: Client, db: Database, main_channel: int, bot_username: str):
        self.client = client
        self.db = db
        self.channel = main_channel
        self.bot_username = bot_username

    async def save_to_library(
        self,
        series_slug: str,
        series_title: str,
        quality: str,
        episode_key: str,
        file_id: str,
        file_unique_id: str,
        poster_url: str | None = None,
        is_movie: bool = False,
    ):
        """
        Save a downloaded file to the main channel library.

        1. Check if a library entry exists for this series_slug + quality
        2. If YES: add episode, check caption length, edit or create new part
        3. If NO: create a new message with poster and episode link
        """
        if not self.channel:
            return

        lock_key = f"{series_slug}:{quality}"
        async with _get_lock(lock_key):
            await self._save_locked(
                series_slug, series_title, quality, episode_key,
                file_id, file_unique_id, poster_url, is_movie,
            )

    async def _save_locked(
        self,
        series_slug: str,
        series_title: str,
        quality: str,
        episode_key: str,
        file_id: str,
        file_unique_id: str,
        poster_url: str | None,
        is_movie: bool,
    ):
        now = datetime.now(timezone.utc).isoformat()

        # Save file mapping
        await self.db.files.update_one(
            {"file_unique_id": file_unique_id},
            {"$set": {
                "file_id": file_id,
                "file_unique_id": file_unique_id,
                "series_slug": series_slug,
                "episode_key": episode_key,
                "quality": quality,
                "updated_at": now,
            }},
            upsert=True,
        )

        # Get all downloaded qualities for this series and episode
        cursor = self.db.files.find({"series_slug": series_slug, "episode_key": episode_key})
        all_files = await cursor.to_list(length=None)
        # sort qualities high to low
        all_files.sort(key=lambda x: {"1080p": 3, "720p": 2, "480p": 1, "360p": 0}.get(x.get("quality", ""), 0), reverse=True)

        entry = await self.db.library.find_one(
            {"series_slug": series_slug, "episode_key": episode_key}
        )

        # Build message components
        caption = self._format_caption(series_title, episode_key, poster_url)
        
        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        
        buttons = []
        row = []
        for f in all_files:
            q = f.get("quality", "Auto")
            deep = f"https://t.me/{self.bot_username}?start=get_{series_slug}_{q}_{episode_key}"
            row.append(InlineKeyboardButton(q, url=deep))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
            
        markup = InlineKeyboardMarkup(buttons)

        if entry and entry.get("message_id"):
            # Update existing message
            msg_id = entry["message_id"]
            try:
                if poster_url:
                    await self.client.edit_message_caption(
                        chat_id=self.channel,
                        message_id=msg_id,
                        caption=caption[:CAPTION_LIMIT],
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=markup,
                    )
                else:
                    await self.client.edit_message_text(
                        chat_id=self.channel,
                        message_id=msg_id,
                        text=caption[:CAPTION_LIMIT],
                        parse_mode=enums.ParseMode.HTML,
                        disable_web_page_preview=True,
                        reply_markup=markup,
                    )
                # Update DB
                await self.db.library.update_one(
                    {"_id": entry["_id"]},
                    {"$set": {"updated_at": now, "poster_url": poster_url}}
                )
                return  # Success! Exit function.
            except Exception as e:
                log.warning("Failed to update library message %d (maybe deleted manually?). Recreating: %s", msg_id, e)
                # Fall through to create a new message
                
        # Create new message (either because it didn't exist, or edit failed)
        try:
            if poster_url:
                msg = await self.client.send_photo(
                    chat_id=self.channel,
                    photo=poster_url,
                    caption=caption[:CAPTION_LIMIT],
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=markup,
                )
            else:
                msg = await self.client.send_message(
                    chat_id=self.channel,
                    text=caption[:CAPTION_LIMIT],
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=markup,
                )
            # Upsert DB
            await self.db.library.update_one(
                {"series_slug": series_slug, "episode_key": episode_key},
                {"$set": {
                    "series_slug": series_slug,
                    "series_title": series_title,
                    "episode_key": episode_key,
                    "message_id": msg.id,
                    "poster_url": poster_url,
                    "updated_at": now,
                }},
                upsert=True
            )
        except Exception as e:
            log.error("Failed to create library message: %s", e)

    def _format_caption(self, title: str, episode_key: str, poster_url: str | None) -> str:
        """Format the caption for a single episode message."""
        import html as htmlmod
        import re
        
        title_esc = htmlmod.escape(title)
        
        season = 1
        is_movie = False
        episode_str = "0"
        
        if episode_key.lower() == "movie":
            is_movie = True
            episode_str = "Movie"
        else:
            m = re.match(r"S(\d+)E(\d+)", episode_key, re.IGNORECASE)
            if m:
                season = int(m.group(1))
                episode_str = str(int(m.group(2)))

        status = "FINISHED"
        genres = "Action, Adventure, Comedy, Drama, Fantasy"
        audio = "Dual Audio" if "Dual" in title or "Hindi" in title else "Japanese"
        if "Hindi" in title:
            audio = "Dual Audio"
            
        caption = (
            f"◆ {title_esc} ◆ ❞\n"
            f"⟐━━━━━━━━━━━━━━━━━⟐\n"
            f"➥ Sᴇᴀsᴏɴ:- {season}\n"
            f"➥ Eᴘɪsᴏᴅᴇ:- {episode_str}\n"
            f"➥ Sᴛᴀᴛᴜs:- {status}\n"
            f"➥ Gᴇɴʀᴇs:- {genres}\n"
            f"➥ Aᴜᴅɪᴏ:- {audio}\n"
            f"⟐━━━━━━━━━━━━━━━━━⟐\n"
            f"⟲ Pᴏᴡᴇʀᴇᴅ ʙʏ:- @{self.bot_username}"
        )
        
        return caption

    async def get_file(self, series_slug: str, quality: str, episode_key: str) -> str | None:
        """Get file_id for a specific episode."""
        entry = await self.db.files.find_one(
            {
                "series_slug": series_slug,
                "quality": quality,
                "episode_key": episode_key,
            },
        )
        if entry:
            return entry.get("file_id")
        return None


def _ep_sort_key(key: str):
    """Sort episode keys like S1E01, S1E02, etc."""
    import re
    m = re.match(r"S(\d+)E(\d+)", key, re.IGNORECASE)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    if key.lower() == "movie":
        return (0, 0)
    return (999, 0)


# Singleton — set during startup
library_manager: LibraryManager | None = None
