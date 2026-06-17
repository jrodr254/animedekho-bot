"""Download manager — yt-dlp (primary) + N_m3u8DL-RE (fallback) + ffmpeg (last resort)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
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
    return f"{sanitize_filename(series_title)} S{season:01d}E{episode:02d} [{quality}].mp4"


def make_movie_filename(movie_title: str, quality: str) -> str:
    return f"{sanitize_filename(movie_title)} [{quality}].mp4"


# ── Stylish Progress ──────────────────────────────────────────────────


def _progress_bar(pct: float, width: int = 12) -> str:
    filled = int(pct / 100 * width)
    return "▓" * filled + "░" * (width - filled)


def _format_size(b: float) -> str:
    if b >= 1024**3: return f"{b / 1024**3:.1f} GB"
    if b >= 1024**2: return f"{b / 1024**2:.1f} MB"
    if b >= 1024: return f"{b / 1024:.1f} KB"
    return f"{b:.0f} B"


def _format_time(s: float) -> str:
    if s < 60: return f"{int(s)}s"
    if s < 3600: return f"{int(s // 60)}m {int(s % 60)}s"
    return f"{int(s // 3600)}h {int((s % 3600) // 60)}m"


def _format_speed(bps: float) -> str:
    if bps >= 1024**2: return f"{bps / 1024**2:.1f} MB/s"
    if bps >= 1024: return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


def _download_progress_text(title, quality, size_bytes, elapsed, speed, engine=""):
    eng = f" ({engine})" if engine else ""
    return (
        f"📥 <b>Downloading{eng}</b>\n"
        f"┌ 📺 {title}\n"
        f"├ 🎬 Quality: {quality}\n"
        f"├ 💾 Size: {_format_size(size_bytes)}\n"
        f"├ ⚡ Speed: {_format_speed(speed) if speed > 0 else 'starting...'}\n"
        f"├ ⏱ Elapsed: {_format_time(elapsed)}\n"
        f"└ 🔄 In progress..."
    )


def _upload_progress_text(title, quality, current, total):
    pct = current / total * 100 if total > 0 else 0
    return (
        f"📤 <b>Uploading to Telegram</b>\n"
        f"┌ 📺 {title}\n"
        f"├ 🎬 Quality: {quality}\n"
        f"├ {_progress_bar(pct)} {pct:.0f}%\n"
        f"├ 💾 {_format_size(current)} / {_format_size(total)}\n"
        f"└ 🔄 Uploading via MTProto..."
    )


def _done_text(title, quality, size_bytes, elapsed):
    return (
        f"✅ <b>Download Complete!</b>\n"
        f"┌ 📺 {title}\n"
        f"├ 🎬 Quality: {quality}\n"
        f"├ 💾 Size: {_format_size(size_bytes)}\n"
        f"└ ⏱ Time: {_format_time(elapsed)}"
    )


async def _update_progress(msg, text, last_edit, interval=4.0):
    now = time.time()
    if now - last_edit[0] < interval:
        return
    last_edit[0] = now
    try:
        await msg.edit_text(text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass


def _get_origin(url: str) -> str:
    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    origin = f"https://{domain}"
    if "megacloud" in domain or "rabbit" in domain:
        origin = "https://megacloud.tv"
    elif "vmeas" in domain or "vidmoly" in domain:
        origin = "https://vidmoly.to"
    return origin


# ── yt-dlp (PRIMARY — works on all architectures) ────────────────────


async def ytdlp_download(
    stream_url: str,
    quality: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
) -> bool:
    """Download HLS stream using yt-dlp. Handles master m3u8 natively."""
    last_edit = [0.0]
    start_time = time.time()

    if progress_msg:
        await _update_progress(progress_msg,
            _download_progress_text(title, quality, 0, 0, 0, "yt-dlp"), [0])

    origin = _get_origin(stream_url)

    # Map quality to height for format selection
    height = quality.replace("p", "") if quality.endswith("p") and quality[:-1].isdigit() else ""

    cmd = [
        "yt-dlp",
        stream_url,
        "-o", output_path,
        "--no-warnings",
        "--no-check-certificates",
        "--referer", f"{origin}/",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--merge-output-format", "mp4",
        "--no-part",
        "--concurrent-fragments", "8",
    ]

    if height:
        # Select specific quality + all audio tracks
        cmd.extend(["-f", f"bv*[height={height}]+ba/bv*[height<={height}]+ba/b"])
    else:
        cmd.extend(["-f", "bv*+ba/b"])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _monitor():
        last_size = 0
        while proc.returncode is None:
            await asyncio.sleep(4)
            try:
                if os.path.exists(output_path):
                    size = os.path.getsize(output_path)
                else:
                    # Check for temp files yt-dlp creates
                    size = sum(f.stat().st_size for f in _TEMP_DIR.glob("**/*")
                               if f.is_file() and Path(output_path).stem in f.name)
                elapsed = time.time() - start_time
                speed = max(0, (size - last_size)) / 4
                last_size = size
                if progress_msg and size > 100_000:
                    await _update_progress(progress_msg,
                        _download_progress_text(title, quality, size, elapsed, speed, "yt-dlp"),
                        last_edit)
            except Exception:
                pass

    monitor_task = asyncio.create_task(_monitor())

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=2400)
    except asyncio.TimeoutError:
        proc.kill()
        monitor_task.cancel()
        log.error("yt-dlp timed out for %s", stream_url[:60])
        return False
    finally:
        monitor_task.cancel()
        try: await monitor_task
        except asyncio.CancelledError: pass

    if proc.returncode != 0:
        err = stderr.decode()[-300:] if stderr else "unknown"
        log.error("yt-dlp failed (%d): %s", proc.returncode, err)
        return False

    # yt-dlp might create .mp4 or different extension
    if not os.path.exists(output_path):
        stem = Path(output_path).stem
        for ext in [".mp4", ".mkv", ".webm"]:
            alt = str(Path(output_path).parent / f"{stem}{ext}")
            if os.path.exists(alt) and alt != output_path:
                os.rename(alt, output_path)
                break

    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


# ── N_m3u8DL-RE (FALLBACK) ───────────────────────────────────────────


async def n_m3u8dl_re_download(
    stream_url: str,
    quality: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
) -> bool:
    """Download using N_m3u8DL-RE (may not work on ARM64)."""
    if not shutil.which("N_m3u8DL-RE"):
        return False

    last_edit = [0.0]
    start_time = time.time()

    stem = Path(output_path).stem
    save_dir = str(Path(output_path).parent)
    origin = _get_origin(stream_url)

    cmd = [
        "N_m3u8DL-RE", stream_url,
        "--save-dir", save_dir, "--save-name", stem,
        "--tmp-dir", str(_SEGMENTS_DIR),
        "--del-after-done", "--thread-count", "16",
        "--download-retry-count", "5", "--binary-merge",
        "--no-ansi-color",
        "--mux-after-done", "format=mp4",
        "--select-audio", "all", "--select-subtitle", "all",
        "--header", f"Referer: {origin}/",
        "--header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    ]

    height = quality.replace("p", "") if quality.endswith("p") and quality[:-1].isdigit() else ""
    if height:
        cmd.extend(["--select-video", f"res={height}*"])
    else:
        cmd.extend(["--auto-select"])

    # Use script PTY wrapper for Spectre.Console crash prevention
    import shlex
    shell_cmd = " ".join(shlex.quote(c) for c in cmd)
    env = os.environ.copy()
    env["TERM"] = "dumb"

    try:
        proc = await asyncio.create_subprocess_exec(
            "script", "-qc", shell_cmd, "/dev/null",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
    except FileNotFoundError:
        # script command not available
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )

    async def _monitor():
        last_size = 0
        while proc.returncode is None:
            await asyncio.sleep(4)
            try:
                total = sum(f.stat().st_size for f in Path(save_dir).glob("**/*")
                           if f.is_file() and (stem in f.name or f.suffix in (".ts", ".m4s", ".mp4")))
                total += sum(f.stat().st_size for f in _SEGMENTS_DIR.glob("**/*") if f.is_file())
                elapsed = time.time() - start_time
                speed = max(0, (total - last_size)) / 4
                last_size = total
                if progress_msg and total > 100_000:
                    await _update_progress(progress_msg,
                        _download_progress_text(title, quality, total, elapsed, speed, "N_m3u8DL-RE"),
                        last_edit)
            except Exception:
                pass

    monitor_task = asyncio.create_task(_monitor())
    try:
        await asyncio.wait_for(proc.wait(), timeout=2400)
    except asyncio.TimeoutError:
        proc.kill()
        monitor_task.cancel()
        return False
    finally:
        monitor_task.cancel()
        try: await monitor_task
        except asyncio.CancelledError: pass

    # Check output (script may return 0 even on failure)
    if not os.path.exists(output_path):
        for ext in [".mp4", ".mkv", ".ts"]:
            alt = str(Path(save_dir) / f"{stem}{ext}")
            if os.path.exists(alt) and alt != output_path:
                os.rename(alt, output_path)
                break

    success = os.path.exists(output_path) and os.path.getsize(output_path) > 0
    if not success:
        log.error("N_m3u8DL-RE produced no output")
    return success


# ── Main download + upload ────────────────────────────────────────────


async def download_and_upload(
    chat_id: int,
    stream_url: str,
    quality: str,
    filename: str,
    title: str,
    progress_msg: Message,
    client: Client,
    variant_url: str = "",
) -> tuple[bool, Message | None]:
    """Download video + upload via Pyrogram MTProto."""
    output_path = str(_TEMP_DIR / filename)
    overall_start = time.time()

    try:
        success = False
        is_m3u8 = ".m3u8" in stream_url.lower()

        if is_m3u8:
            # 1. Try yt-dlp (works on all architectures)
            success = await ytdlp_download(
                stream_url, quality, output_path, progress_msg, title)

            # 2. Try N_m3u8DL-RE (may fail on ARM64)
            if not success:
                log.info("yt-dlp failed, trying N_m3u8DL-RE for %s", title)
                success = await n_m3u8dl_re_download(
                    stream_url, quality, output_path, progress_msg, title)

            if not success:
                log.error("All download methods failed for %s", title)
        else:
            # Direct mp4 — use yt-dlp
            success = await ytdlp_download(
                stream_url, quality, output_path, progress_msg, title)

        if not success:
            await progress_msg.edit_text(
                f"❌ <b>Download Failed</b>\n"
                f"┌ 📺 {title}\n"
                f"└ 💔 Could not download from server",
                parse_mode=enums.ParseMode.HTML)
            return False, None

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            await progress_msg.edit_text(
                f"❌ <b>Download Failed</b>\n"
                f"┌ 📺 {title}\n"
                f"└ 💔 File is empty",
                parse_mode=enums.ParseMode.HTML)
            return False, None

        file_size = os.path.getsize(output_path)

        if file_size > TG_UPLOAD_LIMIT:
            await progress_msg.edit_text(
                f"⚠️ <b>File Too Large</b>\n"
                f"┌ 📺 {title}\n"
                f"├ 💾 {_format_size(file_size)} (max 2 GB)\n"
                f"└ 💡 Try a lower quality",
                parse_mode=enums.ParseMode.HTML)
            return False, None

        # Upload
        upload_last_edit = [0.0]

        async def _upload_progress(current: int, total: int):
            await _update_progress(progress_msg,
                _upload_progress_text(title, quality, current, total),
                upload_last_edit, interval=5.0)

        await progress_msg.edit_text(
            f"📤 <b>Uploading to Telegram</b>\n"
            f"┌ 📺 {title}\n"
            f"├ 🎬 Quality: {quality}\n"
            f"├ 💾 Size: {_format_size(file_size)}\n"
            f"└ 🔄 Starting upload...",
            parse_mode=enums.ParseMode.HTML)

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
            parse_mode=enums.ParseMode.HTML)
        return True, sent_msg

    except Exception as e:
        log.exception("Download/upload error for %s", title)
        try:
            await progress_msg.edit_text(
                f"❌ <b>Error</b>\n"
                f"┌ 📺 {title}\n"
                f"└ 💔 {str(e)[:200]}",
                parse_mode=enums.ParseMode.HTML)
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
