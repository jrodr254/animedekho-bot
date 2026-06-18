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


_SPIN_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_spin_idx = 0

def _spinner() -> str:
    global _spin_idx
    _spin_idx = (_spin_idx + 1) % len(_SPIN_FRAMES)
    return _SPIN_FRAMES[_spin_idx]


def _progress_bar(pct: float, width: int = 20) -> str:
    """Smooth animated progress bar with gradient fill."""
    filled_exact = pct / 100 * width
    filled = int(filled_exact)
    # Sub-block characters for smooth partial fill
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
    bar += "░" * remaining
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
    """Calculate estimated time remaining."""
    if pct <= 0 or elapsed <= 0:
        return "calculating..."
    remaining = elapsed / pct * (100 - pct)
    return _format_time(remaining)


def _download_progress_text(title, quality, pct, size_bytes, elapsed, speed, eta="", engine=""):
    eng = f" · {engine}" if engine else ""
    spin = _spinner()
    speed_str = _format_speed(speed) if speed > 0 else "⏳ starting..."
    bar = _progress_bar(pct)
    eta_str = eta or _calc_eta(pct, elapsed)

    lines = [
        f"{spin} <b>⬇️ Downloading{eng}</b>",
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


def _upload_progress_text(title, quality, current, total, speed=0, elapsed=0):
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


def _done_text(title, quality, size_bytes, elapsed):
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
            _download_progress_text(title, quality, 0, 0, 0, 0, "", "yt-dlp"), [0])

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
        stderr=asyncio.subprocess.STDOUT,
    )

    # Track progress state parsed from yt-dlp output
    _dl_state = {"pct": 0.0, "size": 0, "speed": 0.0, "eta": ""}

    async def _read_output():
        """Parse yt-dlp stdout for real progress info."""
        pct_re = re.compile(r'(\d+(?:\.\d+)?)%')
        size_re = re.compile(r'of\s+~?\s*([\d.]+)(Ki?B|Mi?B|Gi?B)')
        speed_re = re.compile(r'at\s+([\d.]+)(Ki?B|Mi?B|Gi?B)/s')
        eta_re = re.compile(r'ETA\s+(\S+)')
        frag_re = re.compile(r'fragment\s+(\d+)/(\d+)')

        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if not text:
                continue

            # Parse percentage
            m = pct_re.search(text)
            if m:
                _dl_state["pct"] = min(99.0, float(m.group(1)))

            # Parse fragment progress (for HLS)
            m = frag_re.search(text)
            if m:
                done, total = int(m.group(1)), int(m.group(2))
                if total > 0:
                    _dl_state["pct"] = min(99.0, done / total * 100)

            # Parse total size
            m = size_re.search(text)
            if m:
                val = float(m.group(1))
                unit = m.group(2).upper()
                if "G" in unit: val *= 1024**3
                elif "M" in unit: val *= 1024**2
                elif "K" in unit: val *= 1024
                _dl_state["size"] = int(val)

            # Parse speed
            m = speed_re.search(text)
            if m:
                val = float(m.group(1))
                unit = m.group(2).upper()
                if "G" in unit: val *= 1024**3
                elif "M" in unit: val *= 1024**2
                elif "K" in unit: val *= 1024
                _dl_state["speed"] = val

            # Parse ETA
            m = eta_re.search(text)
            if m:
                _dl_state["eta"] = m.group(1)

    _stall_detected = asyncio.Event()
    _STALL_TIMEOUT = 120  # Kill if no new data for 2 minutes

    async def _monitor():
        last_disk_size = 0
        last_disk_time = time.time()
        last_change_time = time.time()
        while proc.returncode is None:
            await asyncio.sleep(3)
            try:
                # Always track actual file size on disk
                disk_size = 0
                if os.path.exists(output_path):
                    disk_size = os.path.getsize(output_path)
                if disk_size == 0:
                    stem = Path(output_path).stem
                    disk_size = sum(
                        f.stat().st_size for f in _TEMP_DIR.glob("**/*")
                        if f.is_file() and (stem in f.name or f.suffix in (".part", ".mp4", ".ts", ".m4s"))
                    )

                # Use disk size as authoritative size
                _dl_state["size"] = max(_dl_state["size"], disk_size)

                # Calculate speed from disk size changes
                now = time.time()
                dt = now - last_disk_time
                if dt > 1:
                    if disk_size > last_disk_size:
                        _dl_state["speed"] = (disk_size - last_disk_size) / dt
                        last_change_time = now
                    elif disk_size == last_disk_size and disk_size > 0:
                        # No progress — check for stall
                        _dl_state["speed"] = 0
                        if now - last_change_time > _STALL_TIMEOUT:
                            log.warning("yt-dlp stalled for %ds, killing", _STALL_TIMEOUT)
                            _stall_detected.set()
                            proc.kill()
                            break
                last_disk_size = disk_size
                last_disk_time = now

                # If yt-dlp didn't give us %, estimate from file size
                if _dl_state["pct"] == 0 and disk_size > 0:
                    est_total = {"1080p": 400, "720p": 200, "480p": 100, "360p": 60, "240p": 30}.get(quality, 200) * 1024 * 1024
                    _dl_state["pct"] = min(95.0, disk_size / est_total * 100)

                elapsed = time.time() - start_time
                stall_info = ""
                if _dl_state["speed"] == 0 and disk_size > 0:
                    stall_secs = int(now - last_change_time)
                    if stall_secs > 10:
                        stall_info = f"\n⚠️ Stalled for {stall_secs}s..."
                if progress_msg:
                    await _update_progress(progress_msg,
                        _download_progress_text(
                            title, quality, _dl_state["pct"],
                            _dl_state["size"], elapsed,
                            _dl_state["speed"], _dl_state["eta"], "yt-dlp") + stall_info,
                        last_edit, interval=3.0)
            except Exception:
                pass

    read_task = asyncio.create_task(_read_output())
    monitor_task = asyncio.create_task(_monitor())

    try:
        await asyncio.wait_for(proc.wait(), timeout=2400)
    except asyncio.TimeoutError:
        proc.kill()
        log.error("yt-dlp timed out for %s", stream_url[:60])
        return False
    finally:
        for t in (read_task, monitor_task):
            t.cancel()
            try: await t
            except asyncio.CancelledError: pass

    if _stall_detected.is_set():
        log.warning("yt-dlp was killed due to stall for %s", stream_url[:60])
        # Clean up partial file so retry starts fresh
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
            stem = Path(output_path).stem
            for f in _TEMP_DIR.glob(f"*{stem}*"):
                f.unlink(missing_ok=True)
        except Exception:
            pass
        return False

    if proc.returncode != 0:
        log.error("yt-dlp failed (%d)", proc.returncode)
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
        last_time = time.time()
        while proc.returncode is None:
            await asyncio.sleep(3)
            try:
                total = sum(f.stat().st_size for f in Path(save_dir).glob("**/*")
                           if f.is_file() and (stem in f.name or f.suffix in (".ts", ".m4s", ".mp4")))
                total += sum(f.stat().st_size for f in _SEGMENTS_DIR.glob("**/*") if f.is_file())
                now = time.time()
                dt = now - last_time
                speed = max(0, (total - last_size)) / dt if dt > 0 else 0
                last_size = total
                last_time = now
                elapsed = now - start_time
                # Estimate % from file size (rough for m3u8)
                est_total = {"1080p": 400, "720p": 200, "480p": 100}.get(quality, 200) * 1024 * 1024
                pct = min(95, total / est_total * 100) if est_total > 0 else 0
                if progress_msg and total > 100_000:
                    await _update_progress(progress_msg,
                        _download_progress_text(title, quality, pct, total, elapsed, speed, "", "N_m3u8DL-RE"),
                        last_edit, interval=3.0)
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
            await _update_progress(progress_msg,
                _upload_progress_text(title, quality, current, total,
                                      upload_speed[0], time.time() - upload_start[0]),
                upload_last_edit, interval=3.0)

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
