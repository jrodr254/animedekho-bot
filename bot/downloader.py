"""Download manager — Multi-engine with N_m3u8DL-RE, Direct HTTP, and FFmpeg fallback."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from pyrogram import Client, enums
from pyrogram.types import Message

from api.models import Quality

log = logging.getLogger(__name__)

TG_UPLOAD_LIMIT = 2 * 1024 * 1024 * 1024

_TEMP_BASE = Path(tempfile.gettempdir()) / "animedekho_dl"
_TEMP_BASE.mkdir(parents=True, exist_ok=True)


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = re.sub(r'[\s]+', ' ', name).strip()
    return name[:120] or "video"


def make_episode_filename(series_title: str, season: int, episode: int, quality: str) -> str:
    return f"{sanitize_filename(series_title)} S{season:01d}E{episode:02d} [{quality}].mp4"


def make_movie_filename(movie_title: str, quality: str) -> str:
    return f"{sanitize_filename(movie_title)} [{quality}].mp4"


# ── Stylish Progress ──────────────────────────────────────────────────


_SPIN_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_spin_idx = 0

def _spinner() -> str:
    global _spin_idx
    _spin_idx = (_spin_idx + 1) % len(_SPIN_FRAMES)
    return _SPIN_FRAMES[_spin_idx]


def _progress_bar(pct: float, width: int = 20) -> str:
    """Smooth animated progress bar with gradient fill."""
    filled_exact = max(0.0, min(100.0, pct)) / 100 * width
    filled = int(filled_exact)
    partials = ["", "▏", "▎", "▍", "▌", "▋", "▊", "▉"]
    partial_idx = int((filled_exact - filled) * len(partials))

    if filled >= width:
        return "█" * width
    bar = "█" * filled
    if partial_idx > 0:
        bar += partials[partial_idx]
        remaining = width - filled - 1
    else:
        remaining = width - filled
    bar += "░" * max(0, remaining)
    return bar


def _format_size(b: float) -> str:
    if b >= 1024**3: return f"{b / 1024**3:.2f} GB"
    if b >= 1024**2: return f"{b / 1024**2:.1f} MB"
    if b >= 1024: return f"{b / 1024:.1f} KB"
    return f"{b:.0f} B"


def _format_time(s: float) -> str:
    if s < 0: return "∞"
    if s < 60: return f"{int(s)}s"
    if s < 3600: return f"{int(s // 60)}m {int(s % 60)}s"
    return f"{int(s // 3600)}h {int((s % 3600) // 60)}m"


def _format_speed(bps: float) -> str:
    if bps >= 1024**2: return f"{bps / 1024**2:.1f} MB/s"
    if bps >= 1024: return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


def _calc_eta(pct: float, elapsed: float) -> str:
    if pct <= 0 or elapsed <= 0:
        return "calculating..."
    remaining = elapsed / pct * (100 - pct)
    return _format_time(remaining)


def _download_progress_text(title: str, quality: str, pct: float, size_bytes: float, elapsed: float, speed: float, eta: str = "") -> str:
    spin = _spinner()
    speed_str = _format_speed(speed) if speed > 0 else "⏳ starting..."
    bar = _progress_bar(pct)
    eta_str = eta or _calc_eta(pct, elapsed)

    lines = [
        f"{spin} <b>⬇️ Downloading</b>",
        f"",
        f"<b>{title}</b>",
        f"🎬 {quality}",
        f"",
        f"<code>{bar}</code> <b>{pct:.1f}%</b>",
        f"",
        f"📦 {_format_size(size_bytes)}  ⚡ {speed_str}",
        f"⏱ {_format_time(elapsed)}  ⏳ ETA: {eta_str}",
    ]
    return "\n".join(lines)


def _upload_progress_text(title: str, quality: str, current: float, total: float, speed: float = 0, elapsed: float = 0) -> str:
    pct = current / total * 100 if total > 0 else 0
    spin = _spinner()
    bar = _progress_bar(pct)
    speed_str = _format_speed(speed) if speed > 0 else "⏳ starting..."
    eta_str = _calc_eta(pct, elapsed) if pct > 0 and elapsed > 0 else "calculating..."

    lines = [
        f"{spin} <b>⬆️ Uploading to Telegram</b>",
        f"",
        f"<b>{title}</b>",
        f"🎬 {quality}",
        f"",
        f"<code>{bar}</code> <b>{pct:.1f}%</b>",
        f"",
        f"📦 {_format_size(current)} / {_format_size(total)}",
        f"⚡ {speed_str}  ⏳ ETA: {eta_str}",
    ]
    return "\n".join(lines)


def _done_text(title: str, quality: str, size_bytes: float, elapsed: float) -> str:
    bar = _progress_bar(100)
    avg_speed = size_bytes / elapsed if elapsed > 0 else 0
    return (
        f"✅ <b>Upload Complete!</b>\n\n"
        f"<b>{title}</b>\n"
        f"🎬 {quality}\n\n"
        f"<code>{bar}</code> <b>100%</b>\n\n"
        f"📦 {_format_size(size_bytes)}  ⚡ avg {_format_speed(avg_speed)}\n"
        f"⏱ Total: {_format_time(elapsed)}"
    )


async def _update_progress(msg: Message | None, text: str, last_edit: list[float], interval: float = 3.0):
    if not msg:
        return
    now = time.time()
    if now - last_edit[0] < interval:
        return
    last_edit[0] = now
    try:
        await msg.edit_text(text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass


def _get_origin(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if not domain:
        return "https://animedekho.app"
    if "megacloud" in domain or "rabbit" in domain or "dokicloud" in domain:
        return "https://megacloud.tv"
    elif "vmeas" in domain or "vidmoly" in domain or "vmbox" in domain:
        return "https://vidmoly.to"
    elif "turboviplay" in domain or "turbosplayer" in domain or "emturbovid" in domain:
        return "https://emturbovid.com"
    elif "xerver" in domain or "vidsrc" in domain or "googleusercontent" in domain:
        return "https://mirror.xerver.xyz"
    return f"https://{domain}"


# ── Engine 1: Direct HTTP Download (MP4 / Direct Streams) ─────────────


async def direct_http_download(
    url: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
    quality: str = "auto",
    referer: str = "",
) -> bool:
    """Download direct video file (MP4/MKV) via chunked HTTP stream with progress."""
    log.info("Direct HTTP download: url=%s quality=%s", url[:120], quality)
    last_edit = [0.0]
    start_time = time.time()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": referer or _get_origin(url) + "/",
        "Accept": "*/*",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=2400, connect=30, sock_read=60)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status not in (200, 206):
                    log.warning("Direct HTTP download failed with status %d", resp.status)
                    return False

                total_bytes = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                last_bytes = 0
                last_time = time.time()

                with open(output_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1MB chunks
                        f.write(chunk)
                        downloaded += len(chunk)

                        now = time.time()
                        dt = now - last_time
                        if dt >= 1.0:
                            speed = (downloaded - last_bytes) / dt
                            last_bytes = downloaded
                            last_time = now
                            elapsed = now - start_time
                            pct = (downloaded / total_bytes * 100) if total_bytes > 0 else 0
                            if progress_msg and downloaded > 50_000:
                                await _update_progress(
                                    progress_msg,
                                    _download_progress_text(title, quality, pct, downloaded, elapsed, speed),
                                    last_edit,
                                    interval=3.0,
                                )

        success = os.path.exists(output_path) and os.path.getsize(output_path) > 0
        if success:
            log.info("Direct HTTP download complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
        return success

    except Exception as e:
        log.warning("Direct HTTP download error: %s", e)
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except Exception: pass
        return False


# ── Engine 2: N_m3u8DL-RE (Multi-audio HLS/DASH) ───────────────────────


async def n_m3u8dl_re_download(
    stream_url: str,
    quality: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
    variant_url: str = "",
) -> bool:
    """Download using N_m3u8DL-RE — video + all audio tracks simultaneously."""
    if not shutil.which("N_m3u8DL-RE"):
        log.warning("N_m3u8DL-RE not found in PATH!")
        return False

    log.info("N_m3u8DL-RE download: url=%s quality=%s", stream_url[:120], quality)

    stem = Path(output_path).stem
    save_dir = str(Path(output_path).parent)
    origin = _get_origin(stream_url)

    job_id = uuid.uuid4().hex[:8]
    job_temp_dir = _TEMP_BASE / f"re_{stem}_{job_id}"
    job_temp_dir.mkdir(parents=True, exist_ok=True)

    height = quality.replace("p", "") if quality.endswith("p") and quality[:-1].isdigit() else ""

    # Attempt 1: Try with resolution selector if height is specified
    async def _run_dl(target_url: str, select_res: bool) -> bool:
        cmd = [
            "N_m3u8DL-RE", target_url,
            "--save-dir", save_dir, "--save-name", stem,
            "--tmp-dir", str(job_temp_dir),
            "--del-after-done",
            "--thread-count", "16",
            "--download-retry-count", "5",
            "--binary-merge",
            "--no-ansi-color",
            "--no-log",
            "-M", "format=mp4",
            "--select-audio", "all",
            "--select-subtitle", "all",
            "--header", f"Referer: {origin}/",
            "--header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        ]

        if select_res and height:
            cmd.extend(["--select-video", f'res=".*{height}.*":for=best'])
        else:
            cmd.extend(["--auto-select"])

        env = os.environ.copy()
        env["TERM"] = "xterm"
        env["DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"] = "1"
        env["DOTNET_SYSTEM_CONSOLE_ALLOW_ANSI_COLOR_REDIRECTION"] = "1"
        env["COMPlus_EnableDiagnostics"] = "0"
        env["DOTNET_EnableDiagnostics"] = "0"

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        last_edit = [0.0]
        start_time = time.time()
        _STALL_TIMEOUT = 120
        _stall_detected = asyncio.Event()

        async def _monitor():
            last_size = 0
            last_time = time.time()
            last_change_time = time.time()
            while proc.returncode is None:
                await asyncio.sleep(3)
                try:
                    total = sum(
                        f.stat().st_size for f in Path(save_dir).glob(f"*{stem}*")
                        if f.is_file()
                    )
                    total += sum(f.stat().st_size for f in job_temp_dir.glob("**/*") if f.is_file())

                    now = time.time()
                    dt = now - last_time
                    speed = max(0, (total - last_size)) / dt if dt > 0 else 0

                    if total > last_size:
                        last_change_time = now
                    elif total == last_size and total > 0:
                        if now - last_change_time > _STALL_TIMEOUT:
                            log.warning("N_m3u8DL-RE stalled for %ds, killing", _STALL_TIMEOUT)
                            _stall_detected.set()
                            proc.kill()
                            break

                    last_size = total
                    last_time = now
                    elapsed = now - start_time

                    est_total = {
                        "1080p": 400, "720p": 200, "480p": 100, "360p": 60, "240p": 30
                    }.get(quality, 200) * 1024 * 1024
                    pct = min(95, total / est_total * 100) if est_total > 0 else 0

                    stall_info = ""
                    if speed == 0 and total > 0:
                        stall_secs = int(now - last_change_time)
                        if stall_secs > 10:
                            stall_info = f"\n⚠️ Stalled for {stall_secs}s..."

                    if progress_msg and total > 100_000:
                        await _update_progress(
                            progress_msg,
                            _download_progress_text(title, quality, pct, total, elapsed, speed) + stall_info,
                            last_edit,
                            interval=3.0,
                        )
                except Exception:
                    pass

        monitor_task = asyncio.create_task(_monitor())
        stdout_data, stderr_data = b"", b""
        try:
            stdout_data, stderr_data = await asyncio.wait_for(proc.communicate(), timeout=1800)
        except asyncio.TimeoutError:
            proc.kill()
            log.error("N_m3u8DL-RE timed out")
            return False
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

        if _stall_detected.is_set():
            return False

        if proc.returncode != 0:
            err_msg = stderr_data.decode("utf-8", errors="ignore").strip() or stdout_data.decode("utf-8", errors="ignore").strip()
            log.warning("N_m3u8DL-RE exit code %d: %s", proc.returncode, err_msg[-500:])

        # Look for produced files
        if not os.path.exists(output_path):
            for ext in [".mp4", ".mkv", ".ts"]:
                alt = str(Path(save_dir) / f"{stem}{ext}")
                if os.path.exists(alt) and alt != output_path:
                    os.rename(alt, output_path)
                    break

        return os.path.exists(output_path) and os.path.getsize(output_path) > 0

    try:
        # Step 1: Run on stream_url with resolution filter
        success = await _run_dl(stream_url, select_res=bool(height))
        if success:
            log.info("N_m3u8DL-RE download complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
            return True

        # Step 2: Retry with auto-select on variant_url (or stream_url) if select_res failed
        target = variant_url or stream_url
        log.info("N_m3u8DL-RE retrying with --auto-select on %s", target[:80])
        success = await _run_dl(target, select_res=False)
        if success:
            log.info("N_m3u8DL-RE auto-select complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
            return True

        return False
    finally:
        shutil.rmtree(job_temp_dir, ignore_errors=True)


# ── Engine 3: FFmpeg Fallback (M3U8 / Media Streams) ───────────────────


async def ffmpeg_download(
    stream_url: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
    quality: str = "auto",
    referer: str = "",
) -> bool:
    """Download stream using FFmpeg as a reliable universal fallback."""
    if not shutil.which("ffmpeg"):
        log.error("FFmpeg not found in PATH!")
        return False

    log.info("FFmpeg fallback download: url=%s quality=%s", stream_url[:120], quality)
    origin = referer or _get_origin(stream_url)

    cmd = ["ffmpeg", "-y"]
    headers = f"Referer: {origin}/\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n"
    cmd.extend(["-headers", headers])
    cmd.extend(["-i", stream_url, "-map", "0", "-c", "copy", "-bsf:a", "aac_adtstoasc", output_path])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    last_edit = [0.0]
    start_time = time.time()

    async def _monitor():
        last_size = 0
        last_time = time.time()
        while proc.returncode is None:
            await asyncio.sleep(3)
            try:
                total = os.path.getsize(output_path) if os.path.exists(output_path) else 0
                now = time.time()
                dt = now - last_time
                speed = max(0, (total - last_size)) / dt if dt > 0 else 0
                last_size = total
                last_time = now
                elapsed = now - start_time

                est_total = {
                    "1080p": 400, "720p": 200, "480p": 100, "360p": 60, "240p": 30
                }.get(quality, 200) * 1024 * 1024
                pct = min(95, total / est_total * 100) if est_total > 0 else 0

                if progress_msg and total > 100_000:
                    await _update_progress(
                        progress_msg,
                        _download_progress_text(title, quality, pct, total, elapsed, speed),
                        last_edit,
                        interval=3.0,
                    )
            except Exception:
                pass

    monitor_task = asyncio.create_task(_monitor())
    try:
        await asyncio.wait_for(proc.wait(), timeout=1800)
    except asyncio.TimeoutError:
        proc.kill()
        log.error("FFmpeg timed out for %s", stream_url[:60])
        return False
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

    success = os.path.exists(output_path) and os.path.getsize(output_path) > 0
    if success:
        log.info("FFmpeg download complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
    return success


# ── Unified Media Downloader ──────────────────────────────────────────


async def download_media(
    stream_url: str,
    quality: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
    variant_url: str = "",
) -> bool:
    """
    Unified multi-engine downloader:
    1. If URL is MP4 / direct file: use direct HTTP stream download.
    2. If URL is M3U8: try N_m3u8DL-RE.
    3. If N_m3u8DL-RE fails or is unavailable: fallback to FFmpeg.
    """
    is_mp4 = (
        ".mp4" in stream_url.lower() or
        ".mkv" in stream_url.lower() or
        "googleusercontent" in stream_url or
        "instant_dl" in stream_url or
        (".m3u8" not in stream_url.lower() and ".m3u8" not in variant_url.lower())
    )

    if is_mp4:
        log.info("Detected direct MP4/file URL, using direct HTTP downloader")
        ok = await direct_http_download(stream_url, output_path, progress_msg, title, quality)
        if ok:
            return True
        log.warning("Direct HTTP download failed, falling back to FFmpeg")
        return await ffmpeg_download(stream_url, output_path, progress_msg, title, quality)

    # M3U8 stream
    if shutil.which("N_m3u8DL-RE"):
        ok = await n_m3u8dl_re_download(stream_url, quality, output_path, progress_msg, title, variant_url=variant_url)
        if ok:
            return True
        log.warning("N_m3u8DL-RE failed for %s, falling back to FFmpeg", stream_url[:60])

    # Fallback to FFmpeg on stream_url or variant_url
    target_url = variant_url or stream_url
    ok = await ffmpeg_download(target_url, output_path, progress_msg, title, quality)
    if not ok and variant_url and variant_url != stream_url:
        ok = await ffmpeg_download(stream_url, output_path, progress_msg, title, quality)

    return ok


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
    """Download video + upload via Pyrogram MTProto with progress."""
    output_path = str(_TEMP_BASE / filename)
    overall_start = time.time()

    try:
        success = await download_media(
            stream_url, quality, output_path, progress_msg, title, variant_url=variant_url
        )

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

        # Upload to Telegram
        upload_last_edit = [0.0]
        upload_start = [0.0]
        upload_last_bytes = [0]
        upload_last_time = [0.0]
        upload_speed = [0.0]

        async def _upload_progress(current: int, total: int):
            if upload_start[0] == 0:
                upload_start[0] = time.time()
                upload_last_time[0] = time.time()
            now = time.time()
            dt = now - upload_last_time[0]
            if dt > 0.5:
                upload_speed[0] = (current - upload_last_bytes[0]) / dt
                upload_last_bytes[0] = current
                upload_last_time[0] = now
            await _update_progress(
                progress_msg,
                _upload_progress_text(title, quality, current, total, upload_speed[0], time.time() - upload_start[0]),
                upload_last_edit,
                interval=3.0,
            )

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
            for f in _TEMP_BASE.glob(f"*{stem}*"):
                if f.is_file():
                    f.unlink(missing_ok=True)
                elif f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
        except Exception:
            pass
