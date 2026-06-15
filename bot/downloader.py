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

# Pyrogram MTProto upload limit (2 GB)
TG_UPLOAD_LIMIT = 2 * 1024 * 1024 * 1024

# Temp directory for downloads
_TEMP_DIR = Path(tempfile.gettempdir()) / "animedekho_dl"
_TEMP_DIR.mkdir(parents=True, exist_ok=True)

# N_m3u8DL-RE tmp segments directory
_SEGMENTS_DIR = _TEMP_DIR / "segments"
_SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)


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


# ── N_m3u8DL-RE: Download m3u8/HLS streams ───────────────────────────


async def n_m3u8dl_re_download(
    stream_url: str,
    quality: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
) -> bool:
    """
    Download an m3u8/HLS stream using N_m3u8DL-RE.

    Args:
        stream_url: The m3u8 stream URL (variant or master playlist)
        quality: Resolution like "720p", "1080p", "480p"
        output_path: Where to save the file (full path with .mp4 extension)
        title: For progress display
    """
    last_edit = [0.0]

    if progress_msg:
        await _update_progress(
            progress_msg,
            f"📥 <b>Downloading:</b> {title} [{quality}]\n"
            f"{_progress_bar(0)}\n⏳ Starting N_m3u8DL-RE...",
            [0],
        )

    # Extract save name without extension
    stem = Path(output_path).stem
    save_dir = str(Path(output_path).parent)

    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(stream_url)
    
    # If the URL is our sidecar decryptor, extract the real target URL for headers
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
        "--del-after-done",           # Clean up segment temp files
        "--thread-count", "16",       # Fast parallel download
        "--download-retry-count", "5",  # Retry failed segments
        "--binary-merge",             # Use binary merge (faster)
        "--mux-after-done", "format=mp4",  # Mux to mp4 using ffmpeg
        "--select-audio", "all",      # Download all available audio tracks
        "--select-subtitle", "all",   # Download all available subtitles
        "--header", f"Referer: {origin}/",
        "--header", f"Origin: {origin}",
        "--header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]

    # The URL is already the specific playlist for the chosen quality (extracted by resolver)
    # or it's a master playlist and the user wants "auto". In both cases, auto-select is correct.
    cmd.extend(["--auto-select"])
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _monitor():
        """Monitor download progress by parsing N_m3u8DL-RE output."""
        buffer = bytearray()
        while True:
            try:
                char = await proc.stdout.read(1)
                if not char:
                    break
                if char in (b'\r', b'\n'):
                    line = buffer.decode('utf-8', errors='replace').strip()
                    buffer.clear()
                    if line and "%" in line:
                        # strip ansi codes
                        line = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', line)
                        percent_match = re.search(r"(\d+(\.\d+)?)%", line)
                        if percent_match:
                            pct_val = percent_match.group(1)
                            speed_match = re.search(r"(\d+(\.\d+)?\s*[MKG]?i?(B/s|bps|b/s|bit/s))", line, re.I)
                            speed_val = speed_match.group(1) if speed_match else "0 MB/s"
                            size_match = re.search(r"(\d+(\.\d+)?\s*\S+)\s*/\s*(\d+(\.\d+)?\s*\S+)", line, re.I)
                            down_size = size_match.group(1) if size_match else "?"
                            total_size = size_match.group(3) if size_match else "?"
                            
                            if progress_msg:
                                await _update_progress(
                                    progress_msg,
                                    f"📥 <b>Downloading:</b> {title} [{quality}]\n"
                                    f"{_progress_bar(float(pct_val))}\n"
                                    f"⚡ Speed: {speed_val}\n"
                                    f"💾 Size: {down_size} / {total_size}",
                                    last_edit,
                                )
                else:
                    buffer.extend(char)
            except Exception:
                break

    monitor_task = asyncio.create_task(_monitor())

    try:
        await asyncio.wait_for(proc.wait(), timeout=900)  # 15 min timeout
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
        log.error("N_m3u8DL-RE failed (%d)", proc.returncode)
        return False

    # N_m3u8DL-RE might create the file with the name directly
    # or with a different extension before muxing. Find it.
    if not os.path.exists(output_path):
        # Check for variants the muxer might create
        for ext in [".mp4", ".mkv", ".ts"]:
            alt = str(Path(save_dir) / f"{stem}{ext}")
            if os.path.exists(alt):
                if alt != output_path:
                    os.rename(alt, output_path)
                break

    return os.path.exists(output_path)


# ── Fallback: ffmpeg direct m3u8/mp4 download ────────────────────────


async def download_m3u8(
    url: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
) -> bool:
    """Fallback: download m3u8/mp4 stream using ffmpeg directly."""
    last_edit = [0.0]

    if progress_msg:
        await _update_progress(
            progress_msg,
            f"📥 <b>Downloading:</b> {title}\n{_progress_bar(0)}\n⏳ Starting ffmpeg...",
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
    stream_url: str,
    quality: str,
    filename: str,
    title: str,
    progress_msg: Message,
    client: Client,
) -> tuple[bool, Message | None]:
    """
    Download a video using N_m3u8DL-RE (primary) or ffmpeg (fallback)
    and upload via Pyrogram MTProto.

    Args:
        stream_url: The m3u8/mp4 stream URL
        quality: Resolution string like "720p"
        filename: Output filename
        title: Display title

    Returns (success, sent_message).
    """
    output_path = str(_TEMP_DIR / filename)

    try:
        success = False

        # Determine if this is an m3u8 stream (use N_m3u8DL-RE) or mp4 (use ffmpeg)
        is_m3u8 = ".m3u8" in stream_url.lower()

        if is_m3u8:
            # Primary: download m3u8 with N_m3u8DL-RE
            success = await n_m3u8dl_re_download(
                stream_url, quality, output_path, progress_msg, title
            )

            # Fallback to ffmpeg if N_m3u8DL-RE fails
            if not success:
                log.info("N_m3u8DL-RE failed, falling back to ffmpeg for %s", title)
                if progress_msg:
                    try:
                        await progress_msg.edit_text(
                            f"📥 <b>Retrying with ffmpeg:</b> {title} [{quality}]",
                            parse_mode=enums.ParseMode.HTML,
                        )
                    except Exception:
                        pass
                success = await download_m3u8(stream_url, output_path, progress_msg, title)
        else:
            # Direct mp4 URL — use ffmpeg
            success = await download_m3u8(stream_url, output_path, progress_msg, title)

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
