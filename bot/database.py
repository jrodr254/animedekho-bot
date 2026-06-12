"""MongoDB integration using motor (async driver)."""

from __future__ import annotations
import logging
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

log = logging.getLogger(__name__)


class Database:
    def __init__(self, mongo_uri: str, db_name: str = "animedekho"):
        self.client = AsyncIOMotorClient(mongo_uri)
        self.db = self.client[db_name]

        # Collections
        self.users = self.db["users"]
        self.library = self.db["library"]
        self.files = self.db["files"]
        self.downloads = self.db["downloads"]
        self.config = self.db["config"]

    async def init_indexes(self):
        """Create necessary indexes."""
        await self.users.create_index("user_id", unique=True)
        await self.library.create_index(
            [("series_slug", 1), ("quality", 1), ("part", 1)],
            unique=True,
        )
        await self.files.create_index("file_unique_id", unique=True)
        await self.files.create_index(
            [("series_slug", 1), ("quality", 1), ("episode_key", 1)],
            unique=True,
        )
        await self.downloads.create_index("user_id")
        await self.downloads.create_index("timestamp")
        log.info("MongoDB indexes created")

    # ── User management ───────────────────────────────────────────

    async def add_user(self, user_id: int, username: str = "", added_by: int = 0) -> bool:
        """Add approved user. Returns True if newly added."""
        try:
            await self.users.insert_one({
                "user_id": user_id,
                "username": username,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "added_by": added_by,
            })
            return True
        except Exception:
            # Duplicate key = already exists
            return False

    async def remove_user(self, user_id: int) -> bool:
        """Remove user. Returns True if removed."""
        result = await self.users.delete_one({"user_id": user_id})
        return result.deleted_count > 0

    async def is_approved(self, user_id: int) -> bool:
        """Check if user is approved."""
        doc = await self.users.find_one({"user_id": user_id})
        return doc is not None

    async def get_users(self) -> list[int]:
        """Get all approved user IDs."""
        cursor = self.users.find({}, {"user_id": 1})
        return sorted([doc["user_id"] async for doc in cursor])

    # ── Config (channel invite link, etc.) ────────────────────────

    async def get_config(self, key: str, default=None):
        """Get a config value."""
        doc = await self.config.find_one({"_id": key})
        return doc["value"] if doc else default

    async def set_config(self, key: str, value):
        """Set a config value."""
        await self.config.update_one(
            {"_id": key},
            {"$set": {"value": value}},
            upsert=True,
        )

    # ── File cache (duplicate prevention) ────────────────────────

    async def get_cached_file(
        self, series_slug: str, quality: str, episode_key: str
    ) -> str | None:
        """
        Check if this exact series+quality+episode was already downloaded.
        Returns file_id if cached, None otherwise.
        """
        doc = await self.files.find_one({
            "series_slug": series_slug,
            "quality": quality,
            "episode_key": episode_key,
        })
        return doc["file_id"] if doc else None

    async def save_file(
        self,
        series_slug: str,
        series_title: str,
        quality: str,
        episode_key: str,
        file_id: str,
        file_unique_id: str,
    ):
        """Save a downloaded file reference for future cache lookups."""
        try:
            await self.files.update_one(
                {
                    "series_slug": series_slug,
                    "quality": quality,
                    "episode_key": episode_key,
                },
                {"$set": {
                    "series_title": series_title,
                    "file_id": file_id,
                    "file_unique_id": file_unique_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
        except Exception as e:
            log.warning("Failed to save file cache: %s", e)

    # ── Download logging ──────────────────────────────────────────

    async def log_download(
        self,
        user_id: int,
        series_slug: str,
        episode: str,
        quality: str,
        file_id: str,
    ):
        """Log a download to history."""
        await self.downloads.insert_one({
            "user_id": user_id,
            "series_slug": series_slug,
            "episode": episode,
            "quality": quality,
            "file_id": file_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def close(self):
        """Close the MongoDB connection."""
        self.client.close()


# Singleton — set during post_init
db: Database | None = None
