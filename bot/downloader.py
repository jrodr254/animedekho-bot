"""Download manager — uses yt-dlp for streaming servers + Pyrogram MTProto for 2GB uploads."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path

from pyrogram import Client, enums
from pyrogram.types import Message

from api.models import Quality

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
        pass


# ── yt-dlp: List available qualities from a player URL ────────────────


async def ytdlp_get_qualities(player_url: str) -> list[Quality]:
    """
    Use yt-dlp to probe a streaming server URL and return available qualities.
    Each Quality has a format_id that yt-dlp can use to download that specific format.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--dump-json", "--no-download",
            "--no-warnings", "--no-playlist",
            player_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode != 0:
            log.debug("yt-dlp probe failed for %s: %s", player_url[:60], stderr.decode()[-200:])
            return []

        data = json.loads(stdout.decode())
        formats = data.get("formats", [])

        qualities = []
        seen = set()
        for f in formats:
            height = f.get("height")
            if not height or height in seen:
                continue
            seen.add(height)

            fid = f.get("format_id", str(height))
            tbr = int(f.get("tbr") or f.get("vbr") or 0)
            res = f"{height}p"
            label = res
            if height >= 1080:
                label = f"{res} (FHD)"
            elif height >= 720:
                label = f"{res} (HD)"
            elif height <= 480:
                label = f"{res} (SD)"

            qualities.append(Quality(
                resolution=res,
                url=player_url,  # yt-dlp will handle the actual download
                bandwidth=tbr * 1000,
                label=label,
            ))

        # Sort by resolution descending
        qualities.sort(key=lambda q: int(q.resolution.replace("p", "")), reverse=True)
        return qualities

    except asyncio.TimeoutError:
        log.warning("yt-dlp probe timed out for %s", player_url[:60])
        return []
    except Exception as e:
        log.warning("yt-dlp probe error for %s: %s", player_url[:60], e)
        return []


async def ytdlp_probe_servers(servers: list) -> tuple[list[Quality], str]:
    """
    Try yt-dlp on each server until one returns qualities.
    Returns (qualities, working_player_url) or ([], "").
    """
    for srv in servers:
        url = srv.player_url or srv.direct_url
        if not url:
            continue
        log.info("Probing server %s with yt-dlp: %s", srv.name, url[:60])
        qualities = await ytdlp_get_qualities(url)
        if qualities:
            log.info("yt-dlp found %d qualities on %s", len(qualities), srv.name)
            return qualities, url
    return [], ""


# ── yt-dlp: Download with specific quality ────────────────────────────


async def ytdlp_download(
    player_url: str,
    quality: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
) -> bool:
    """
    Download from a streaming server using yt-dlp.
    
    Args:
        player_url: The streaming server embed/player URL
        quality: Resolution like "720p", "1080p", "480p"
        output_path: Where to save the file
        title: For progress display
    """
    last_edit = [0.0]

    if progress_msg:
        await _update_progress(
            progress_msg,
            f"📥 <b>Downloading:</b> {title} [{quality}]\n"
            f"{_progress_bar(0)}\n⏳ Starting yt-dlp...",
            [0],
        )

    # Build format selector for yt-dlp
    height = quality.replace("p", "")
    # Try exact height, then best available at or below
    format_sel = f"bv*[height={height}]+ba/bv*[height<={height}]+ba/b[height<={height}]/b"

    proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "-f", format_sel,
        "--merge-output-format", "mp4",
        "--all-subs",              # All subtitles
        "--embed-subs",            # Embed subtitles in file
        "--audio-multistreams",    # All audio tracks
        "--no-playlist",
        "--no-warnings",
        "--newline",               # Progress on new lines
        "-o", output_path,
        player_url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _monitor():
        """Monitor yt-dlp stdout for progress updates."""
        start = time.time()
        while proc.returncode is None:
            await asyncio.sleep(3)
            if os.path.exists(output_path):
                size_mb = os.path.getsize(output_path) / (1024 * 1024)
                elapsed = time.time() - start
                if progress_msg:
                    await _update_progress(
                        progress_msg,
                        f"📥 <b>Downloading:</b> {title} [{quality}]\n"
                        f"💾 {size_mb:.1f} MB downloaded\n"
                        f"⏱ {elapsed:.0f}s elapsed",
                        last_edit,
                    )
            else:
                # Check for partial files
                partials = list(_TEMP_DIR.glob(f"*{Path(output_path).stem}*"))
                if partials:
                    total_size = sum(p.stat().st_size for p in partials if p.exists())
                    size_mb = total_size / (1024 * 1024)
                    elapsed = time.time() - start
                    if progress_msg:
                        await _update_progress(
                            progress_msg,
                            f"📥 <b>Downloading:</b> {title} [{quality}]\n"
                            f"💾 {size_mb:.1f} MB downloaded\n"
                            f"⏱ {elapsed:.0f}s elapsed",
                            last_edit,
                        )

    monitor_task = asyncio.create_task(_monitor())

    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=900)  # 15 min timeout
    except asyncio.TimeoutError:
        proc.kill()
        monitor_task.cancel()
        log.error("yt-dlp timed out for %s", player_url[:60])
        return False
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

    if proc.returncode != 0:
        log.error("yt-dlp failed (%d): %s", proc.returncode, stderr.decode()[-500:])
        return False

    # yt-dlp may create file with different extension, find it
    if not os.path.exists(output_path):
        # Check for .mp4 or other variants
        stem = Path(output_path).stem
        for ext in [".mp4", ".mkv", ".webm"]:
            alt = str(_TEMP_DIR / f"{stem}{ext}")
            if os.path.exists(alt):
                os.rename(alt, output_path)
                break

    return os.path.exists(output_path)


# ── Fallback: ffmpeg direct m3u8 download ─────────────────────────────


async def download_m3u8(
    url: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
) -> bool:
    """Fallback: download m3u8 stream using ffmpeg directly."""
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
        "-map", "0:v?",
        "-map", "0:a?",
        "-map", "0:s?",
        "-c:v", "copy",
        "-c:a", "copy",
        "-c:s", "mov_text",
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


# ── Main download + upload function ───────────────────────────────────


async def download_and_upload(
    chat_id: int,
    player_url: str,
    quality: str,
    filename: str,
    title: str,
    progress_msg: Message,
    client: Client,
) -> tuple[bool, Message | None]:
    """
    Download a video using yt-dlp from a streaming server and upload via Pyrogram MTProto.
    
    Args:
        player_url: The streaming server embed URL
        quality: Resolution string like "720p"
        filename: Output filename
        title: Display title
    
    Returns (success, sent_message).
    """
    output_path = str(_TEMP_DIR / filename)

    try:
        # Primary: download with yt-dlp
        success = await ytdlp_download(player_url, quality, output_path, progress_msg, title)

        if not success:
            await progress_msg.edit_text(
                f"❌ <b>Download failed:</b> {title}",
                parse_mode=enums.ParseMode.HTML,
            )
            return False, None

        # Check file
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            await progress_msg.edit_text(
                f"❌ <b>Download failed:</b> {title}\nFile is empty.",
                parse_mode=enums.ParseMode.HTML,
            )
            return False, None

        file_size = os.path.getsize(output_path)
        file_size_mb = file_size / (1024 * 1024)

        if file_size > TG_UPLOAD_LIMIT:
            await progress_msg.edit_text(
                f"⚠️ <b>{title}</b>\n"
                f"File too large ({file_size_mb:.1f} MB > 2048 MB limit).\n"
                f"Try a lower quality.",
                parse_mode=enums.ParseMode.HTML,
            )
            return False, None

        # Upload via Pyrogram MTProto
        upload_last_edit = [0.0]

        async def _upload_progress(current: int, total: int):
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

        sent_msg = await client.send_document(
            chat_id=chat_id,
            document=output_path,
            file_name=filename,
            caption=f"📺 {title} [{quality}]",
            progress=_upload_progress,
        )

        await progress_msg.edit_text(
            f"✅ <b>Done:</b> {title} [{quality}]\n💾 {file_size_mb:.1f} MB",
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
        # Clean up temp files
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
            # Clean partial files
            stem = Path(output_path).stem
            for f in _TEMP_DIR.glob(f"*{stem}*"):
                f.unlink(missing_ok=True)
        except Exception:
            pass
