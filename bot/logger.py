"""Bot logger — sends formatted events to the log channel."""

from __future__ import annotations
import json
import logging
from pathlib import Path

from telegram import Bot
from telegram.constants import ParseMode

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CONFIG_FILE = _DATA_DIR / "config.json"


class BotLogger:
    def __init__(self, bot: Bot):
        self._bot = bot
        self._log_channel: int = 0
        self._main_channel: int = 0
        self._load_config()

    def _load_config(self):
        from config.settings import settings
        self._log_channel = settings.bot.log_channel
        self._main_channel = settings.bot.main_channel
        # Override with persisted config if exists
        try:
            if _CONFIG_FILE.exists():
                data = json.loads(_CONFIG_FILE.read_text())
                if data.get("log_channel"):
                    self._log_channel = int(data["log_channel"])
                if data.get("main_channel"):
                    self._main_channel = int(data["main_channel"])
        except Exception as e:
            log.warning("Failed to load channel config: %s", e)

    def _save_config(self):
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            _CONFIG_FILE.write_text(json.dumps({
                "log_channel": self._log_channel,
                "main_channel": self._main_channel,
            }, indent=2))
        except Exception as e:
            log.warning("Failed to save channel config: %s", e)

    @property
    def log_channel(self) -> int:
        return self._log_channel

    @property
    def main_channel(self) -> int:
        return self._main_channel

    def set_log_channel(self, channel_id: int):
        self._log_channel = channel_id
        self._save_config()

    def set_main_channel(self, channel_id: int):
        self._main_channel = channel_id
        self._save_config()

    async def _send_log(self, text: str):
        if not self._log_channel:
            return
        try:
            await self._bot.send_message(
                self._log_channel, text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as e:
            log.warning("Log channel send failed: %s", e)

    async def _send_main(self, text: str):
        if not self._main_channel:
            return
        try:
            await self._bot.send_message(
                self._main_channel, text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as e:
            log.warning("Main channel send failed: %s", e)

    # ── Log methods ────────────────────────────────────────────────

    async def log_search(self, user_id: int, username: str, query: str):
        await self._send_log(
            f"🔍 <b>Search:</b> <code>{_esc(query)}</code>\n"
            f"👤 @{_esc(username)} (<code>{user_id}</code>)"
        )

    async def log_server_resolve(self, title: str, server_name: str, success: bool):
        icon = "✅" if success else "❌"
        await self._send_log(
            f"▶️ <b>Resolve {icon}:</b> {_esc(title)} — {_esc(server_name)}"
        )

    async def log_error(self, context: str, error: str):
        await self._send_log(
            f"⚠️ <b>Error in {_esc(context)}:</b>\n<code>{_esc(error[:500])}</code>"
        )

    async def log_user_added(self, user_id: int):
        await self._send_log(f"👤 User <code>{user_id}</code> <b>added</b> by owner")

    async def log_user_removed(self, user_id: int):
        await self._send_log(f"👤 User <code>{user_id}</code> <b>removed</b> by owner")

    async def log_bot_start(self, user_id: int, username: str):
        await self._send_log(
            f"🤖 <b>/start</b> by @{_esc(username)} (<code>{user_id}</code>)"
        )

    async def log_download_start(self, user_id: int, username: str, title: str, quality: str):
        await self._send_log(
            f"📥 <b>Download started:</b> {_esc(title)} [{_esc(quality)}]\n"
            f"👤 @{_esc(username)} (<code>{user_id}</code>)"
        )

    async def log_download_complete(self, title: str, quality: str, file_size_mb: float):
        await self._send_log(
            f"✅ <b>Download complete:</b> {_esc(title)} [{_esc(quality)}]\n"
            f"💾 {file_size_mb:.1f} MB"
        )

    async def log_download_error(self, title: str, error: str):
        await self._send_log(
            f"❌ <b>Download failed:</b> {_esc(title)}\n"
            f"<code>{_esc(error[:300])}</code>"
        )

    async def log_batch_start(self, user_id: int, username: str, series: str, season: int, episode_count: int):
        await self._send_log(
            f"📦 <b>Batch download started:</b> {_esc(series)} S{season}\n"
            f"📺 {episode_count} episodes\n"
            f"👤 @{_esc(username)} (<code>{user_id}</code>)"
        )

    async def log_batch_complete(self, series: str, season: int, completed: int, total: int):
        await self._send_log(
            f"📦 <b>Batch complete:</b> {_esc(series)} S{season}\n"
            f"✅ {completed}/{total} episodes uploaded"
        )


def _esc(text: str) -> str:
    import html
    return html.escape(str(text))


# Singleton — set during post_init
bot_logger: BotLogger | None = None
