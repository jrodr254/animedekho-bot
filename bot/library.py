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

        # Find existing library entry (latest part)
        entry = await self.db.library.find_one(
            {"series_slug": series_slug, "quality": quality},
            sort=[("part", -1)],
        )

        ep_data = {
            "file_id": file_id,
            "file_unique_id": file_unique_id,
            "added_at": now,
        }

        if entry:
            # Add episode to existing entry
            episodes = entry.get("episodes", {})
            episodes[episode_key] = ep_data

            # Check if caption would exceed limit
            test_entry = {**entry, "episodes": episodes}
            caption = self._format_caption(test_entry)

            if len(caption) > CAPTION_LIMIT - 20:
                # Caption too long — create a new part
                new_part = entry.get("part", 1) + 1
                new_entry = {
                    "series_slug": series_slug,
                    "series_title": series_title,
                    "quality": quality,
                    "part": new_part,
                    "poster_url": poster_url or entry.get("poster_url"),
                    "episodes": {episode_key: ep_data},
                    "movie_file": ep_data if is_movie else None,
                    "message_id": None,
                    "updated_at": now,
                }
                msg_id = await self._create_library_message(new_entry)
                new_entry["message_id"] = msg_id
                await self.db.library.insert_one(new_entry)
            else:
                # Update existing entry
                update_fields = {
                    f"episodes.{episode_key}": ep_data,
                    "updated_at": now,
                }
                if is_movie:
                    update_fields["movie_file"] = ep_data
                if poster_url and not entry.get("poster_url"):
                    update_fields["poster_url"] = poster_url

                await self.db.library.update_one(
                    {"_id": entry["_id"]},
                    {"$set": update_fields},
                )

                # Re-fetch to get updated episodes for caption
                entry = await self.db.library.find_one({"_id": entry["_id"]})
                if entry and entry.get("message_id"):
                    await self._update_library_message(entry)
                elif entry:
                    msg_id = await self._create_library_message(entry)
                    await self.db.library.update_one(
                        {"_id": entry["_id"]},
                        {"$set": {"message_id": msg_id}},
                    )
        else:
            # Create new entry
            new_entry = {
                "series_slug": series_slug,
                "series_title": series_title,
                "quality": quality,
                "part": 1,
                "poster_url": poster_url,
                "episodes": {episode_key: ep_data},
                "movie_file": ep_data if is_movie else None,
                "message_id": None,
                "updated_at": now,
            }
            msg_id = await self._create_library_message(new_entry)
            new_entry["message_id"] = msg_id
            await self.db.library.insert_one(new_entry)

    async def _create_library_message(self, entry: dict) -> int | None:
        """Send new message to main channel with poster and episode links."""
        caption = self._format_caption(entry)
        try:
            poster = entry.get("poster_url")
            if poster:
                msg = await self.client.send_photo(
                    chat_id=self.channel,
                    photo=poster,
                    caption=caption[:CAPTION_LIMIT],
                    parse_mode=enums.ParseMode.HTML,
                )
            else:
                msg = await self.client.send_message(
                    chat_id=self.channel,
                    text=caption,
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            log.info("Created library message %d for %s [%s]",
                     msg.id, entry["series_slug"], entry["quality"])
            return msg.id
        except Exception as e:
            log.error("Failed to create library message: %s", e)
            return None

    async def _update_library_message(self, entry: dict) -> None:
        """Edit existing message caption to include new episode link."""
        caption = self._format_caption(entry)
        msg_id = entry.get("message_id")
        if not msg_id:
            return
        try:
            if entry.get("poster_url"):
                await self.client.edit_message_caption(
                    chat_id=self.channel,
                    message_id=msg_id,
                    caption=caption[:CAPTION_LIMIT],
                    parse_mode=enums.ParseMode.HTML,
                )
            else:
                await self.client.edit_message_text(
                    chat_id=self.channel,
                    message_id=msg_id,
                    text=caption,
                    parse_mode=enums.ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            log.info("Updated library message %d for %s [%s]",
                     msg_id, entry["series_slug"], entry["quality"])
        except Exception as e:
            log.error("Failed to update library message %d: %s", msg_id, e)

    def _format_caption(self, entry: dict) -> str:
        """
        Format the caption for a library message.
        Style matches the template with decorated dividers and info section.
        """
        import html as htmlmod
        title = htmlmod.escape(entry.get("series_title", entry["series_slug"]))
        quality = entry.get("quality", "auto")
        part = entry.get("part", 1)
        episodes = entry.get("episodes", {})
        movie_file = entry.get("movie_file")
        slug = entry["series_slug"]
        genres = entry.get("genres", [])
        
        # Decorative divider
        divider = "◆━━━━━━━━━━━━━━━━━◆"
        
        # Genre tags
        genre_tags = ""
        if genres:
            genre_tags = "  ".join([f"〘{g}〙" for g in genres[:4]]) + "\n\n"
        
        # Movie format
        if movie_file:
            deep = f"https://t.me/{self.bot_username}?start=get_{slug}_{quality}_movie"
            caption = (
                f"<b>{title}</b>\n\n"
                f"{genre_tags}"
                f"◆ <i>{title}</i> ◆\n\n"
                f"{divider}\n\n"
                f"➡ <b>TYPE:-</b> Movie\n"
                f"➡ <b>QUALITY:-</b> {quality}\n"
                f"➡ <b>AUDIO:-</b> Hindi Dubbed\n\n"
                f"{divider}\n\n"
                f"▶️ <a href=\"{deep}\">【 DOWNLOAD 】</a>"
            )
            return caption
        
        # Series format
        sorted_eps = sorted(episodes.keys(), key=lambda k: _ep_sort_key(k))
        
        # Extract season/episode counts
        seasons_set = set()
        for ep_key in sorted_eps:
            import re
            m = re.match(r"S(\d+)E", ep_key, re.IGNORECASE)
            if m:
                seasons_set.add(int(m.group(1)))
        
        season_count = len(seasons_set) if seasons_set else 1
        ep_count = len(sorted_eps)
        
        # Build episode links
        ep_links = []
        for ep_key in sorted_eps:
            deep = f"https://t.me/{self.bot_username}?start=get_{slug}_{quality}_{ep_key}"
            ep_links.append(f"<a href=\"{deep}\">{ep_key}</a>")
        
        # Join episodes (comma separated, or newlines if many)
        if len(ep_links) <= 10:
            eps_text = " • ".join(ep_links)
        else:
            eps_text = "\n".join([f"➤ {link}" for link in ep_links])
        
        part_text = f" (Part {part})" if part > 1 else ""
        
        caption = (
            f"<b>{title}</b>\n\n"
            f"{genre_tags}"
            f"◆ <i>{title}</i> ◆{part_text}\n\n"
            f"{divider}\n\n"
            f"➡ <b>SEASON:-</b> {season_count}\n"
            f"➡ <b>EPISODE:-</b> {ep_count}\n"
            f"➡ <b>QUALITY:-</b> {quality}\n"
            f"➡ <b>AUDIO:-</b> Hindi Dubbed\n\n"
            f"{divider}\n\n"
            f"📂 <b>Episodes:</b>\n{eps_text}"
        )
        
        return caption

    async def get_file(self, series_slug: str, quality: str, episode_key: str) -> str | None:
        """Get file_id for a specific episode."""
        entry = await self.db.library.find_one(
            {
                "series_slug": series_slug,
                "quality": quality,
                f"episodes.{episode_key}": {"$exists": True},
            },
        )
        if entry:
            ep = entry["episodes"].get(episode_key)
            return ep["file_id"] if ep else None
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
