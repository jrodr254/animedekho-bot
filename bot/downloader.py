"""Download manager — handles video downloads and Telegram uploads via Pyrogram (MTProto)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from pathlib import Path

import aiohttp
from pyrogram import Client, enums
from pyrogram.types import Message

from api.models import Quality
from utils.http import http_client

log = logging.getLogger(__name__)

# Pyrogram MTProto upload limit (2 GB)
TG_UPLOAD_LIMIT = 2 * 1024 * 1024 * 1024

# Temp directory for downloads
_TEMP_DIR = Path(tempfile.gettempdir()) / "animedekho_dl"
_TEMP_DIR.mkdir(parents=True, exist_ok=True)


def sanitize_filename(name: str) -> str:
    """Remove special characters from filename, keep it clean."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = re.sub(r'[\s]+', ' ', name).strip()
    name = name[:120]
    return name or "video"


def make_episode_filename(
    series_title: str, season: int, episode: int, quality: str
) -> str:
    title = sanitize_filename(series_title)
    return f"{title} S{season:01d}E{episode:02d} [{quality}].mp4"


def make_movie_filename(movie_title: str, quality: str) -> str:
    title = sanitize_filename(movie_title)
    return f"{title} [{quality}].mp4"


def _progress_bar(pct: float) -> str:
    """Generate a text progress bar."""
    filled = int(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {pct:.0f}%"


async def _update_progress(msg: Message, text: str, last_edit: list[float]):
    """Edit message with rate limiting (min 3s between edits)."""
    now = time.time()
    if now - last_edit[0] < 3:
        return
    last_edit[0] = now
    try:
        await msg.edit_text(text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass  # Message unchanged or deleted


async def download_m3u8(
    url: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
) -> bool:
    """Download m3u8 stream using ffmpeg, converting to mp4."""
    last_edit = [0.0]

    if progress_msg:
        await _update_progress(
            progress_msg,
            f"📥 <b>Downloading:</b> {title}\n{_progress_bar(0)}\n⏳ Starting ffmpeg...",
            [0],
        )

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-i", url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        output_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _monitor():
        start = time.time()
        while proc.returncode is None:
            await asyncio.sleep(3)
            if os.path.exists(output_path):
                size_mb = os.path.getsize(output_path) / (1024 * 1024)
                elapsed = time.time() - start
                if progress_msg:
                    await _update_progress(
                        progress_msg,
                        f"📥 <b>Downloading:</b> {title}\n"
                        f"💾 {size_mb:.1f} MB downloaded\n"
                        f"⏱ {elapsed:.0f}s elapsed",
                        last_edit,
                    )

    monitor_task = asyncio.create_task(_monitor())

    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        monitor_task.cancel()
        log.error("ffmpeg timed out for %s", url)
        return False
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

    if proc.returncode != 0:
        log.error("ffmpeg failed (%d): %s", proc.returncode, stderr.decode()[-500:])
        return False

    return True


async def download_mp4(
    url: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
) -> bool:
    """Download mp4 file with aiohttp streaming."""
    last_edit = [0.0]

    try:
        await http_client.start()
        session = http_client.session

        async with session.get(url) as resp:
            if resp.status != 200:
                log.error("MP4 download failed: HTTP %d for %s", resp.status, url)
                return False

            total = resp.content_length or 0
            downloaded = 0

            with open(output_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)

                    if progress_msg and total > 0:
                        pct = min(downloaded / total * 100, 99)
                        size_mb = downloaded / (1024 * 1024)
                        total_mb = total / (1024 * 1024)
                        await _update_progress(
                            progress_msg,
                            f"📥 <b>Downloading:</b> {title}\n"
                            f"{_progress_bar(pct)}\n"
                            f"💾 {size_mb:.1f} / {total_mb:.1f} MB",
                            last_edit,
                        )
                    elif progress_msg:
                        size_mb = downloaded / (1024 * 1024)
                        await _update_progress(
                            progress_msg,
                            f"📥 <b>Downloading:</b> {title}\n"
                            f"💾 {size_mb:.1f} MB downloaded",
                            last_edit,
                        )
        return True
    except Exception as e:
        log.error("MP4 download error for %s: %s", url, e)
        return False


async def download_and_upload(
    chat_id: int,
    quality: Quality,
    filename: str,
    title: str,
    progress_msg: Message,
    client: Client,
) -> tuple[bool, Message | None]:
    """
    Download a video (m3u8 or mp4) and upload it to Telegram via Pyrogram (MTProto).
    Supports up to 2GB uploads.
    Returns (success, sent_message).
    """
    output_path = str(_TEMP_DIR / filename)
    video_url = quality.url
    is_m3u8 = ".m3u8" in video_url.lower()

    try:
        # Download
        if is_m3u8:
            success = await download_m3u8(video_url, output_path, progress_msg, title)
        else:
            success = await download_mp4(video_url, output_path, progress_msg, title)

        if not success:
            await progress_msg.edit_text(
                f"❌ <b>Download failed:</b> {title}",
                parse_mode=enums.ParseMode.HTML,
            )
            return False, None

        # Check file size
        file_size = os.path.getsize(output_path)
        file_size_mb = file_size / (1024 * 1024)

        if file_size == 0:
            await progress_msg.edit_text(
                f"❌ <b>Download failed:</b> {title}\nFile is empty.",
                parse_mode=enums.ParseMode.HTML,
            )
            return False, None

        if file_size > TG_UPLOAD_LIMIT:
            await progress_msg.edit_text(
                f"⚠️ <b>{title}</b>\n"
                f"File too large for Telegram ({file_size_mb:.1f} MB > 2048 MB limit).\n"
                f"Try a lower quality.",
                parse_mode=enums.ParseMode.HTML,
            )
            return False, None

        # Upload via Pyrogram (MTProto — supports up to 2GB)
        upload_last_edit = [0.0]

        async def _upload_progress(current: int, total: int):
            """Pyrogram upload progress callback."""
            pct = current / total * 100 if total > 0 else 0
            current_mb = current / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            await _update_progress(
                progress_msg,
                f"📤 <b>Uploading:</b> {title}\n"
                f"{_progress_bar(pct)}\n"
                f"💾 {current_mb:.1f} / {total_mb:.1f} MB",
                upload_last_edit,
            )

        await progress_msg.edit_text(
            f"📤 <b>Uploading:</b> {title}\n💾 {file_size_mb:.1f} MB",
            parse_mode=enums.ParseMode.HTML,
        )

        # Use send_document for reliability (Pyrogram handles chunked upload via MTProto)
        sent_msg = await client.send_document(
            chat_id=chat_id,
            document=output_path,
            file_name=filename,
            caption=f"📺 {title} [{quality.resolution}]",
            progress=_upload_progress,
        )

        await progress_msg.edit_text(
            f"✅ <b>Done:</b> {title}\n💾 {file_size_mb:.1f} MB",
            parse_mode=enums.ParseMode.HTML,
        )
        return True, sent_msg

    except Exception as e:
        log.exception("Download/upload error for %s", title)
        try:
            await progress_msg.edit_text(
                f"❌ <b>Error:</b> {title}\n{str(e)[:200]}",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        return False, None
    finally:
        # Clean up temp file
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except Exception:
            pass
