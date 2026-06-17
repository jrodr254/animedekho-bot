"""Download manager — uses N_m3u8DL-RE for m3u8 streams + ffmpeg fallback + Pyrogram MTProto for 2GB uploads."""

from __future__ import annotations

import asyncio
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

TG_UPLOAD_LIMIT = 2 * 1024 * 1024 * 1024

_TEMP_DIR = Path(tempfile.gettempdir()) / "animedekho_dl"
_TEMP_DIR.mkdir(parents=True, exist_ok=True)

_SEGMENTS_DIR = _TEMP_DIR / "segments"
_SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = re.sub(r'[\s]+', ' ', name).strip()
    return name[:120] or "video"


def make_episode_filename(series_title: str, season: int, episode: int, quality: str) -> str:
    title = sanitize_filename(series_title)
    return f"{title} S{season:01d}E{episode:02d} [{quality}].mp4"


def make_movie_filename(movie_title: str, quality: str) -> str:
    title = sanitize_filename(movie_title)
    return f"{title} [{quality}].mp4"


# ── Stylish Progress ──────────────────────────────────────────────────


def _progress_bar(pct: float, width: int = 12) -> str:
    """Beautiful animated progress bar."""
    filled = int(pct / 100 * width)
    empty = width - filled
    bar = "▓" * filled + "░" * empty
    return bar


def _format_size(bytes_val: float) -> str:
    if bytes_val >= 1024 * 1024 * 1024:
        return f"{bytes_val / (1024**3):.1f} GB"
    elif bytes_val >= 1024 * 1024:
        return f"{bytes_val / (1024**2):.1f} MB"
    elif bytes_val >= 1024:
        return f"{bytes_val / 1024:.1f} KB"
    return f"{bytes_val:.0f} B"


def _format_time(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def _format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / (1024**2):.1f} MB/s"
    elif bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


def _download_progress_text(title: str, quality: str, size_bytes: float,
                            elapsed: float, speed: float, phase: str = "download") -> str:
    """Generate stylish download progress message."""
    size_str = _format_size(size_bytes)
    elapsed_str = _format_time(elapsed)
    speed_str = _format_speed(speed) if speed > 0 else "calculating..."

    if phase == "download":
        icon = "📥"
        action = "Downloading"
    elif phase == "muxing":
        icon = "🔄"
        action = "Muxing"
    elif phase == "upload":
        icon = "📤"
        action = "Uploading"
    else:
        icon = "⏳"
        action = "Processing"

    return (
        f"{icon} <b>{action}</b>\n"
        f"┌ 📺 {title}\n"
        f"├ 🎬 Quality: {quality}\n"
        f"├ 💾 Size: {size_str}\n"
        f"├ ⚡ Speed: {speed_str}\n"
        f"├ ⏱ Elapsed: {elapsed_str}\n"
        f"└ 🔄 Status: In progress..."
    )


def _upload_progress_text(title: str, quality: str, current: int, total: int) -> str:
    """Generate stylish upload progress message."""
    pct = current / total * 100 if total > 0 else 0
    bar = _progress_bar(pct)
    current_str = _format_size(current)
    total_str = _format_size(total)

    return (
        f"📤 <b>Uploading to Telegram</b>\n"
        f"┌ 📺 {title}\n"
        f"├ 🎬 Quality: {quality}\n"
        f"├ {bar} {pct:.0f}%\n"
        f"├ 💾 {current_str} / {total_str}\n"
        f"└ 🔄 Uploading via MTProto..."
    )


def _done_text(title: str, quality: str, size_bytes: float, elapsed: float) -> str:
    """Generate stylish completion message."""
    size_str = _format_size(size_bytes)
    time_str = _format_time(elapsed)

    return (
        f"✅ <b>Download Complete!</b>\n"
        f"┌ 📺 {title}\n"
        f"├ 🎬 Quality: {quality}\n"
        f"├ 💾 Size: {size_str}\n"
        f"└ ⏱ Time: {time_str}"
    )


async def _update_progress(msg: Message, text: str, last_edit: list[float], interval: float = 4.0):
    """Edit message with rate limiting."""
    now = time.time()
    if now - last_edit[0] < interval:
        return
    last_edit[0] = now
    try:
        await msg.edit_text(text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass


# ── N_m3u8DL-RE ──────────────────────────────────────────────────────


async def n_m3u8dl_re_download(
    stream_url: str,
    quality: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
) -> bool:
    """Download m3u8/HLS stream using N_m3u8DL-RE."""
    last_edit = [0.0]
    start_time = time.time()

    if progress_msg:
        await _update_progress(
            progress_msg,
            _download_progress_text(title, quality, 0, 0, 0),
            [0],
        )

    stem = Path(output_path).stem
    save_dir = str(Path(output_path).parent)

    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(stream_url)
    if "localhost" in parsed.netloc or "127.0.0.1" in parsed.netloc:
        qs = parse_qs(parsed.query)
        real_url = qs.get("url", [stream_url])[0]
        domain = urlparse(real_url).netloc
    else:
        domain = parsed.netloc

    origin = f"https://{domain}"
    if "megacloud" in domain or "rabbit" in domain or "dokicloud" in domain:
        origin = "https://megacloud.tv"
    elif "vmeas" in domain or "vidmoly" in domain:
        origin = "https://vidmoly.to"

    cmd = [
        "N_m3u8DL-RE",
        stream_url,
        "--save-dir", save_dir,
        "--save-name", stem,
        "--tmp-dir", str(_SEGMENTS_DIR),
        "--del-after-done",
        "--thread-count", "16",
        "--download-retry-count", "5",
        "--binary-merge",
        "--no-ansi-color",
        "--mux-after-done", "format=mp4",
        "--select-audio", "all",
        "--select-subtitle", "all",
        "--header", f"Referer: {origin}/",
        "--header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]

    if quality and quality not in ("auto",):
        height = quality.replace("p", "") if quality.endswith("p") else ""
        if height.isdigit():
            cmd.extend(["--select-video", f"res={height}*"])
        else:
            cmd.extend(["--auto-select"])
    else:
        cmd.extend(["--auto-select"])

    # TERM=dumb prevents Spectre.Console crash in headless environments
    env = os.environ.copy()
    env["TERM"] = "dumb"

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    async def _monitor():
        last_size = 0
        while proc.returncode is None:
            await asyncio.sleep(4)
            try:
                total_size = 0
                for f in Path(save_dir).glob("**/*"):
                    if f.is_file() and (stem in f.name or f.suffix in (".ts", ".m4s", ".mp4", ".m4a")):
                        total_size += f.stat().st_size
                # Also check segments dir
                for f in _SEGMENTS_DIR.glob("**/*"):
                    if f.is_file():
                        total_size += f.stat().st_size

                elapsed = time.time() - start_time
                speed = max(0, (total_size - last_size)) / 4  # bytes per second
                last_size = total_size

                if progress_msg and total_size > 100_000:
                    await _update_progress(
                        progress_msg,
                        _download_progress_text(title, quality, total_size, elapsed, speed),
                        last_edit,
                    )
            except Exception:
                pass

    monitor_task = asyncio.create_task(_monitor())

    try:
        await asyncio.wait_for(proc.wait(), timeout=2400)
    except asyncio.TimeoutError:
        proc.kill()
        monitor_task.cancel()
        log.error("N_m3u8DL-RE timed out for %s", stream_url[:60])
        return False
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

    if proc.returncode != 0:
        stderr = await proc.stderr.read()
        log.error("N_m3u8DL-RE failed (%d): %s", proc.returncode, stderr.decode()[-300:])
        return False

    if not os.path.exists(output_path):
        for ext in [".mp4", ".mkv", ".ts"]:
            alt = str(Path(save_dir) / f"{stem}{ext}")
            if os.path.exists(alt):
                if alt != output_path:
                    os.rename(alt, output_path)
                break

    return os.path.exists(output_path)


# ── ffmpeg fallback ───────────────────────────────────────────────────


async def download_m3u8(
    url: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
    quality: str = "auto",
) -> bool:
    """Fallback: download m3u8/mp4 stream using ffmpeg."""
    last_edit = [0.0]
    start_time = time.time()

    if progress_msg:
        await _update_progress(
            progress_msg,
            _download_progress_text(title, quality, 0, 0, 0),
            [0],
        )

    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    if "localhost" in parsed.netloc or "127.0.0.1" in parsed.netloc:
        qs = parse_qs(parsed.query)
        real_url = qs.get("url", [url])[0]
        domain = urlparse(real_url).netloc
    else:
        domain = parsed.netloc

    origin = f"https://{domain}"
    if "megacloud" in domain or "rabbit" in domain or "dokicloud" in domain:
        origin = "https://megacloud.tv"
    elif "vmeas" in domain or "vidmoly" in domain:
        origin = "https://vidmoly.to"

    headers = f"Referer: {origin}/\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\n"

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-headers", headers,
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
        last_size = 0
        while proc.returncode is None:
            await asyncio.sleep(4)
            if os.path.exists(output_path):
                size = os.path.getsize(output_path)
                elapsed = time.time() - start_time
                speed = max(0, (size - last_size)) / 4
                last_size = size
                if progress_msg:
                    await _update_progress(
                        progress_msg,
                        _download_progress_text(title, quality, size, elapsed, speed),
                        last_edit,
                    )

    monitor_task = asyncio.create_task(_monitor())

    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=2400)
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


# ── Main download + upload ────────────────────────────────────────────


async def download_and_upload(
    chat_id: int,
    stream_url: str,
    quality: str,
    filename: str,
    title: str,
    progress_msg: Message,
    client: Client,
) -> tuple[bool, Message | None]:
    """Download video + upload via Pyrogram MTProto. Returns (success, sent_message)."""
    output_path = str(_TEMP_DIR / filename)
    overall_start = time.time()

    try:
        success = False
        is_m3u8 = ".m3u8" in stream_url.lower()

        if is_m3u8:
            success = await n_m3u8dl_re_download(
                stream_url, quality, output_path, progress_msg, title
            )
            if not success:
                log.info("N_m3u8DL-RE failed, falling back to ffmpeg for %s", title)
                if progress_msg:
                    try:
                        await progress_msg.edit_text(
                            f"🔄 <b>Switching to ffmpeg...</b>\n"
                            f"┌ 📺 {title}\n"
                            f"└ ⏳ N_m3u8DL-RE failed, retrying...",
                            parse_mode=enums.ParseMode.HTML,
                        )
                    except Exception:
                        pass
                success = await download_m3u8(stream_url, output_path, progress_msg, title, quality)
        else:
            success = await download_m3u8(stream_url, output_path, progress_msg, title, quality)

        if not success:
            await progress_msg.edit_text(
                f"❌ <b>Download Failed</b>\n"
                f"┌ 📺 {title}\n"
                f"└ 💔 Could not download from server",
                parse_mode=enums.ParseMode.HTML,
            )
            return False, None

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            await progress_msg.edit_text(
                f"❌ <b>Download Failed</b>\n"
                f"┌ 📺 {title}\n"
                f"└ 💔 File is empty",
                parse_mode=enums.ParseMode.HTML,
            )
            return False, None

        file_size = os.path.getsize(output_path)
        file_size_str = _format_size(file_size)

        if file_size > TG_UPLOAD_LIMIT:
            await progress_msg.edit_text(
                f"⚠️ <b>File Too Large</b>\n"
                f"┌ 📺 {title}\n"
                f"├ 💾 {file_size_str} (max 2 GB)\n"
                f"└ 💡 Try a lower quality",
                parse_mode=enums.ParseMode.HTML,
            )
            return False, None

        # Upload
        upload_last_edit = [0.0]

        async def _upload_progress(current: int, total: int):
            await _update_progress(
                progress_msg,
                _upload_progress_text(title, quality, current, total),
                upload_last_edit,
                interval=5.0,
            )

        await progress_msg.edit_text(
            f"📤 <b>Uploading to Telegram</b>\n"
            f"┌ 📺 {title}\n"
            f"├ 🎬 Quality: {quality}\n"
            f"├ 💾 Size: {file_size_str}\n"
            f"└ 🔄 Starting upload...",
            parse_mode=enums.ParseMode.HTML,
        )

        sent_msg = await client.send_document(
            chat_id=chat_id,
            document=output_path,
            file_name=filename,
            caption=f"📺 {title} [{quality}]",
            progress=_upload_progress,
        )

        total_time = time.time() - overall_start
        await progress_msg.edit_text(
            _done_text(title, quality, file_size, total_time),
            parse_mode=enums.ParseMode.HTML,
        )
        return True, sent_msg

    except Exception as e:
        log.exception("Download/upload error for %s", title)
        try:
            await progress_msg.edit_text(
                f"❌ <b>Error</b>\n"
                f"┌ 📺 {title}\n"
                f"└ 💔 {str(e)[:200]}",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        return False, None
    finally:
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
            stem = Path(output_path).stem
            for f in _TEMP_DIR.glob(f"*{stem}*"):
                f.unlink(missing_ok=True)
        except Exception:
            pass
