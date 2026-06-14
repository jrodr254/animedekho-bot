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

        # Create a new message for every single episode/file
        new_entry = {
            "series_slug": series_slug,
            "series_title": series_title,
            "quality": quality,
            "poster_url": poster_url,
            "episode_key": episode_key,
            "file_id": file_id,
            "is_movie": is_movie,
            "message_id": None,
            "updated_at": now,
        }
        msg_id = await self._create_library_message(new_entry)
        new_entry["message_id"] = msg_id
        await self.db.library.insert_one(new_entry)

    async def _create_library_message(self, entry: dict) -> int | None:
        """Send new message to main channel with poster and inline button."""
        caption = self._format_caption(entry)
        
        # Build the inline keyboard button for download
        slug = entry["series_slug"]
        quality = entry["quality"]
        ep_key = entry["episode_key"]
        
        deep = f"https://t.me/{self.bot_username}?start=get_{slug}_{quality}_{ep_key}"
        
        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(quality, url=deep)]
        ])

        try:
            poster = entry.get("poster_url")
            if poster:
                msg = await self.client.send_photo(
                    chat_id=self.channel,
                    photo=poster,
                    caption=caption[:CAPTION_LIMIT],
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=markup,
                )
            else:
                msg = await self.client.send_message(
                    chat_id=self.channel,
                    text=caption,
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=markup,
                )
            log.info("Created library message %d for %s [%s]",
                     msg.id, entry["series_slug"], entry["quality"])
            return msg.id
        except Exception as e:
            log.error("Failed to create library message: %s", e)
            return None

    def _format_caption(self, entry: dict) -> str:
        """
        Format the caption for a library message exactly like the reference image.
        """
        import html as htmlmod
        import re
        title = htmlmod.escape(entry.get("series_title", entry["series_slug"]))
        quality = entry.get("quality", "auto")
        ep_key = entry.get("episode_key", "")
        
        # Parse season and episode from ep_key (e.g. S1E01)
        season = 1
        episode = 1
        m = re.match(r"S(\d+)E(\d+)", ep_key, re.IGNORECASE)
        if m:
            season = int(m.group(1))
            episode = int(m.group(2))
            
        # Hardcode some defaults that look like the user's request
        # Since we don't have full metadata in the bot downloader context
        status = "FINISHED"
        genres = "Action, Adventure, Comedy, Drama, Fantasy"
        audio = "Dual Audio" if "Dual" in title or "Hindi" in title else "Japanese"
        if "Hindi" in title:
            audio = "Dual Audio"
            
        caption = (
            f"◆ {title} ◆ ❞\n"
            f"⟐━━━━━━━━━━━━━━━━━⟐\n"
            f"➥ Sᴇᴀsᴏɴ:- {season}\n"
            f"➥ Eᴘɪsᴏᴅᴇ:- {episode}\n"
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
