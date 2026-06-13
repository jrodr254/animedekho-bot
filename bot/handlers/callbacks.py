"""Callback query router — handles all inline button presses."""

from __future__ import annotations
import asyncio
import logging
import os

from pyrogram import Client, enums
from pyrogram.types import CallbackQuery

from api.client import api
from api.models import Quality
from bot import keyboards as kb
from bot.auth import require_approved
from bot.logger import bot_logger
from bot.downloader import (
    download_and_upload, make_episode_filename, make_movie_filename,
    sanitize_filename,
)
from extractors.resolver import resolve_player_url, get_m3u8_qualities
from utils.helpers import esc, truncate, short_slug, extract_series_slug, slug_to_title

log = logging.getLogger(__name__)


@require_approved
async def callback_router(client: Client, query: CallbackQuery):
    await query.answer()
    data = query.data

    try:
        if data == "m:main":
            await _send_text(query, "🎌 <b>AnimeDekho Bot</b>\n\nChoose an option:", kb.main_menu())

        elif data == "m:genres":
            await _handle_genres(query)

        elif data.startswith("rp:"):
            await _handle_series_listing(query, int(data.split(":")[1]))

        elif data.startswith("mp:"):
            await _handle_movies_listing(query, int(data.split(":")[1]))

        elif data.startswith("sr:"):
            await _handle_series_detail(client, query, data[3:])

        elif data.startswith("mr:"):
            await _handle_movie_detail(client, query, data[3:])

        elif data.startswith("se:"):
            parts = data.split(":")
            await _handle_season(query, slug=parts[1], season=int(parts[2]))

        elif data.startswith("ep:"):
            await _handle_episode(query, data[3:])

        elif data.startswith("dl:"):
            # dl:server_idx:quality_idx:ep_slug
            parts = data.split(":", 3)
            await _handle_download(
                client, query,
                server_idx=int(parts[1]),
                quality_idx=int(parts[2]),
                ep_slug=parts[3],
            )

        elif data.startswith("mdl:"):
            # mdl:server_idx:quality_idx:movie_slug
            parts = data.split(":", 3)
            await _handle_movie_download(
                client, query,
                server_idx=int(parts[1]),
                quality_idx=int(parts[2]),
                movie_slug=parts[3],
            )

        elif data.startswith("bat:"):
            # bat:series_slug:season — show quality picker
            parts = data.split(":", 2)
            await _handle_batch_picker(query, slug=parts[1], season=int(parts[2]))

        elif data.startswith("bq:"):
            # bq:series_slug:season:quality — execute batch download
            parts = data.split(":", 3)
            await _handle_batch_download(
                client, query,
                slug=parts[1],
                season=int(parts[2]),
                quality_pref=parts[3],
            )

        elif data.startswith("ct:"):
            parts = data.split(":")
            await _handle_category(query, cat_slug=parts[1], page=int(parts[2]))

    except Exception as e:
        log.exception("Callback error for %s", data)
        if bot_logger:
            await bot_logger.log_error(f"callback:{data}", str(e))
        await _safe_edit(query, f"⚠️ Error: {esc(str(e)[:200])}\n\nTry /start")


# ── Handler implementations ───────────────────────────────────────

async def _handle_series_listing(q: CallbackQuery, page: int):
    result = await api.get_recent_series(page)
    if not result.items:
        await _send_text(q, "No series found.")
        return
    markup = kb.listing_page(result.items, page, result.max_page, "rp", "sr", "📺")
    await _send_text(q, f"📺 <b>Recent Series</b> — Page {page}/{result.max_page}", markup)


async def _handle_movies_listing(q: CallbackQuery, page: int):
    result = await api.get_recent_movies(page)
    if not result.items:
        await _send_text(q, "No movies found.")
        return
    markup = kb.listing_page(result.items, page, result.max_page, "mp", "mr", "🎬")
    await _send_text(q, f"🎬 <b>Movies</b> — Page {page}/{result.max_page}", markup)


async def _handle_series_detail(client: Client, q: CallbackQuery, slug: str):
    series = await api.get_series(slug)

    text = f"📺 <b>{esc(series.title)}</b>\n\n"
    if series.genres:
        text += f"🏷 {', '.join(series.genres[:6])}\n"
    if series.description:
        text += f"\n{esc(truncate(series.description, 350))}\n"

    if not series.seasons:
        text += "\n⚠️ No episodes found on this page."
        markup = kb.main_menu()
    else:
        text += f"\n📂 <b>{series.season_count} Season(s)</b> · {series.total_episodes} episodes\nSelect a season:"
        markup = kb.season_picker(series)

    if series.poster:
        try:
            await q.message.delete()
        except Exception:
            pass
        await client.send_photo(
            chat_id=q.message.chat.id,
            photo=series.poster,
            caption=text[:1024],
            parse_mode=enums.ParseMode.HTML,
            reply_markup=markup,
        )
    else:
        await _send_text(q, text, markup)


async def _handle_movie_detail(client: Client, q: CallbackQuery, slug: str):
    movie = await api.get_movie(slug)

    # Resolve servers
    resolved = await api.resolve_all_servers(movie.servers)
    movie.servers = resolved

    # Populate qualities for servers with player URLs
    await _populate_server_qualities(resolved)

    # Log resolution
    if bot_logger:
        for srv in movie.servers:
            await bot_logger.log_server_resolve(movie.title, srv.name, srv.is_available)

    text = f"🎬 <b>{esc(movie.title)}</b>\n\n"
    if movie.genres:
        text += f"🏷 {', '.join(movie.genres[:6])}\n"
    if movie.description:
        text += f"\n{esc(truncate(movie.description, 350))}\n"

    if resolved:
        text += f"\n▶️ <b>{len(resolved)} server(s) available</b>"
    else:
        text += "\n⚠️ No direct servers found — use 'Watch on Site'."

    # Store for download callbacks
    _store_servers(q.message.chat.id, f"movie:{slug}", resolved, movie.title)

    markup = kb.movie_detail(movie)

    if movie.poster:
        try:
            await q.message.delete()
        except Exception:
            pass
        await client.send_photo(
            chat_id=q.message.chat.id,
            photo=movie.poster,
            caption=text[:1024],
            parse_mode=enums.ParseMode.HTML,
            reply_markup=markup,
        )
    else:
        await _send_text(q, text, markup)


async def _handle_season(q: CallbackQuery, slug: str, season: int):
    series = await api.get_series(slug)
    s = series.seasons.get(season)
    if not s:
        await _safe_edit(q, f"⚠️ Season {season} not found.")
        return

    text = (
        f"📺 <b>{esc(series.title)}</b>\n"
        f"📂 Season {season} — {s.episode_count} episode(s)\n\n"
        f"Select an episode:"
    )
    markup = kb.episode_picker(slug, season, s.episodes)

    try:
        await q.edit_message_caption(caption=text[:1024], parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception:
        await _safe_edit(q, text, markup)


async def _handle_episode(q: CallbackQuery, ep_slug: str):
    episode = await api.get_episode(ep_slug)

    # Resolve all servers
    resolved = await api.resolve_all_servers(episode.servers)

    # Populate qualities for resolved servers
    await _populate_server_qualities(resolved)

    # Log resolution
    if bot_logger:
        for srv in resolved:
            await bot_logger.log_server_resolve(
                episode.title or ep_slug, srv.name, srv.is_available
            )

    text = f"▶️ <b>{esc(episode.title)}</b>\n\n"

    if resolved:
        text += f"🖥 <b>{len(resolved)} server(s) found:</b>\n"
        for srv in resolved[:7]:
            if srv.qualities:
                quals = ", ".join(sq.resolution for sq in srv.qualities[:4])
                text += f"  • {srv.name} — {quals}\n"
            elif srv.player_url:
                from urllib.parse import urlparse
                domain = urlparse(srv.player_url).netloc
                text += f"  • {srv.name} — {domain}\n"
            elif srv.direct_url:
                text += f"  • {srv.name} — Direct {srv.video_type.upper()}\n"
    else:
        text += "⚠️ Could not extract servers. Use 'Watch on Site'.\n"
        if bot_logger:
            await bot_logger.log_error("episode_resolve", f"No servers for {ep_slug}")

    series_slug = extract_series_slug(ep_slug)
    back_cb = f"sr:{short_slug(series_slug)}" if series_slug else "rp:1"

    # Store resolved servers in context for download callbacks
    _store_servers(q.message.chat.id, ep_slug, resolved, episode.title)

    markup = kb.server_picker(resolved, ep_slug, back_cb)

    try:
        await q.edit_message_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception:
        try:
            await q.edit_message_caption(caption=text[:1024], parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            await q.message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)


async def _handle_download(client: Client, q: CallbackQuery, server_idx: int, quality_idx: int, ep_slug: str):
    """Handle single episode download."""
    chat_id = q.message.chat.id
    user = q.from_user

    # Get stored server data or re-resolve
    data = _get_servers(chat_id, ep_slug)
    if not data:
        await _safe_edit(q, "⏳ Resolving servers...")
        episode = await api.get_episode(ep_slug)
        resolved = await api.resolve_all_servers(episode.servers)
        await _populate_server_qualities(resolved)
        _store_servers(chat_id, ep_slug, resolved, episode.title)
        data = _get_servers(chat_id, ep_slug)

    if not data or server_idx >= len(data["servers"]):
        await _safe_edit(q, "⚠️ Server not found. Please go back and try again.")
        return

    srv = data["servers"][server_idx]
    title = data.get("title", ep_slug)

    # Determine quality/URL to download
    quality = _pick_quality(srv, quality_idx)
    if not quality:
        await _safe_edit(q, "⚠️ No downloadable URL found for this server.")
        return

    # Extract season/episode info for filename
    import re
    m = re.match(r".*-(\d+)x(\d+)", ep_slug)
    season = int(m.group(1)) if m else 0
    ep_num = int(m.group(2)) if m else 0
    series_slug = extract_series_slug(ep_slug)
    series_title = slug_to_title(series_slug) if series_slug else title

    filename = make_episode_filename(series_title, season, ep_num, quality.resolution)
    episode_key = f"S{season}E{ep_num:02d}" if season and ep_num else ""

    # ── Duplicate check: send cached file if already downloaded ──
    from bot.database import db
    if db and series_slug and episode_key:
        cached_fid = await db.get_cached_file(series_slug, quality.resolution, episode_key)
        if cached_fid:
            try:
                await q.message.reply_video(
                    video=cached_fid,
                    caption=f"📦 <b>{esc(title)}</b> [{quality.resolution}]\n<i>From library — no download needed!</i>",
                    parse_mode=enums.ParseMode.HTML,
                )
                return
            except Exception as e:
                log.warning("Cached file send failed, will re-download: %s", e)

    # Get poster from stored data
    poster_url = None

    # Log
    if bot_logger:
        await bot_logger.log_download_start(
            user.id, user.username or str(user.id), title, quality.resolution
        )

    # Send progress message and start download in background
    progress_msg = await q.message.reply_text(
        f"📥 <b>Starting download:</b> {esc(title)} [{quality.resolution}]",
        parse_mode=enums.ParseMode.HTML,
    )

    asyncio.create_task(
        _do_download(
            client, chat_id, quality, filename, title, progress_msg, user,
            series_slug=series_slug or "",
            episode_key=episode_key,
            poster_url=poster_url,
        )
    )


async def _handle_movie_download(client: Client, q: CallbackQuery, server_idx: int, quality_idx: int, movie_slug: str):
    """Handle movie download."""
    chat_id = q.message.chat.id
    user = q.from_user

    # Get stored server data or re-resolve
    data = _get_servers(chat_id, f"movie:{movie_slug}")
    if not data:
        await _safe_edit(q, "⏳ Resolving servers...")
        movie = await api.get_movie(movie_slug)
        resolved = await api.resolve_all_servers(movie.servers)
        await _populate_server_qualities(resolved)
        _store_servers(chat_id, f"movie:{movie_slug}", resolved, movie.title)
        data = _get_servers(chat_id, f"movie:{movie_slug}")

    if not data or server_idx >= len(data["servers"]):
        await _safe_edit(q, "⚠️ Server not found. Please go back and try again.")
        return

    srv = data["servers"][server_idx]
    title = data.get("title", slug_to_title(movie_slug))

    quality = _pick_quality(srv, quality_idx)
    if not quality:
        await _safe_edit(q, "⚠️ No downloadable URL found for this server.")
        return

    filename = make_movie_filename(title, quality.resolution)

    # ── Duplicate check for movies ──
    from bot.database import db
    if db:
        cached_fid = await db.get_cached_file(movie_slug, quality.resolution, "movie")
        if cached_fid:
            try:
                await q.message.reply_video(
                    video=cached_fid,
                    caption=f"📦 <b>{esc(title)}</b> [{quality.resolution}]\n<i>From library — no download needed!</i>",
                    parse_mode=enums.ParseMode.HTML,
                )
                return
            except Exception as e:
                log.warning("Cached movie file send failed, will re-download: %s", e)

    if bot_logger:
        await bot_logger.log_download_start(
            user.id, user.username or str(user.id), title, quality.resolution
        )

    progress_msg = await q.message.reply_text(
        f"📥 <b>Starting download:</b> {esc(title)} [{quality.resolution}]",
        parse_mode=enums.ParseMode.HTML,
    )

    asyncio.create_task(
        _do_download(
            client, chat_id, quality, filename, title, progress_msg, user,
            series_slug=movie_slug,
            episode_key="movie",
            is_movie=True,
        )
    )


async def _handle_batch_picker(q: CallbackQuery, slug: str, season: int):
    """Show quality picker for batch download."""
    text = (
        f"📦 <b>Batch Download</b>\n"
        f"📺 {esc(slug_to_title(slug))} — Season {season}\n\n"
        f"Select quality for all episodes:"
    )
    markup = kb.batch_quality_picker(slug, season)
    await _safe_edit(q, text, markup)


async def _handle_batch_download(client: Client, q: CallbackQuery, slug: str, season: int, quality_pref: str):
    """Execute batch download for an entire season."""
    chat_id = q.message.chat.id
    user = q.from_user

    # Get series info
    series = await api.get_series(slug)
    s = series.seasons.get(season)
    if not s or not s.episodes:
        await _safe_edit(q, f"⚠️ No episodes found for Season {season}.")
        return

    total = len(s.episodes)

    if bot_logger:
        await bot_logger.log_batch_start(
            user.id, user.username or str(user.id),
            series.title, season, total
        )

    progress_msg = await q.message.reply_text(
        f"📦 <b>Batch Download Starting</b>\n"
        f"📺 {esc(series.title)} — Season {season}\n"
        f"📂 {total} episodes · Quality: {quality_pref}\n\n"
        f"⏳ Resolving episodes...",
        parse_mode=enums.ParseMode.HTML,
    )

    # Run batch in background
    asyncio.create_task(
        _do_batch_download(
            client, chat_id, series, season, s.episodes,
            quality_pref, progress_msg, user
        )
    )


async def _do_batch_download(client: Client, chat_id, series, season, episodes, quality_pref, progress_msg, user):
    """Execute batch download sequentially."""
    total = len(episodes)
    completed = 0

    for i, ep in enumerate(episodes, 1):
        try:
            await progress_msg.edit_text(
                f"📦 <b>Batch Download</b> — {esc(series.title)} S{season}\n\n"
                f"📥 Downloading S{season}E{ep.number}... ({i}/{total})\n"
                f"✅ {completed} completed",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

        try:
            # ── Check cache first ──
            ep_key = f"S{season}E{ep.number:02d}"
            from bot.database import db
            if db:
                cached_fid = await db.get_cached_file(series.slug, quality_pref, ep_key)
                if cached_fid:
                    try:
                        await client.send_video(
                            chat_id,
                            video=cached_fid,
                            caption=f"📦 <b>{esc(series.title)} {ep_key}</b> [{quality_pref}]\n<i>From library</i>",
                            parse_mode=enums.ParseMode.HTML,
                        )
                        completed += 1
                        continue
                    except Exception:
                        pass  # Cache miss or file expired, download normally

            # Resolve episode
            episode = await api.get_episode(ep.slug)
            resolved = await api.resolve_all_servers(episode.servers)
            await _populate_server_qualities(resolved)

            if not resolved:
                log.warning("No servers for batch ep %s", ep.slug)
                continue

            # Find best matching quality
            quality = _find_quality_match(resolved, quality_pref)
            if not quality:
                log.warning("No quality match for batch ep %s", ep.slug)
                continue

            filename = make_episode_filename(series.title, season, ep.number, quality.resolution)

            # Create a per-episode progress message
            ep_msg = await client.send_message(
                chat_id,
                f"📥 <b>Downloading:</b> S{season}E{ep.number} [{quality.resolution}]",
                parse_mode=enums.ParseMode.HTML,
            )

            success, sent_msg = await download_and_upload(
                chat_id, quality, filename,
                f"{series.title} S{season}E{ep.number}",
                ep_msg, client,
            )

            if success:
                completed += 1
                if bot_logger:
                    await bot_logger.log_download_complete(
                        f"{series.title} S{season}E{ep.number}",
                        quality.resolution, 0
                    )

                # Save to library
                if sent_msg:
                    file_id = None
                    file_unique_id = None
                    if sent_msg.video:
                        file_id = sent_msg.video.file_id
                        file_unique_id = sent_msg.video.file_unique_id
                    elif sent_msg.document:
                        file_id = sent_msg.document.file_id
                        file_unique_id = sent_msg.document.file_unique_id

                    if file_id and file_unique_id:
                        from bot.library import library_manager
                        if library_manager:
                            try:
                                ep_key = f"S{season}E{ep.number:02d}"
                                await library_manager.save_to_library(
                                    series_slug=series.slug,
                                    series_title=series.title,
                                    quality=quality.resolution,
                                    episode_key=ep_key,
                                    file_id=file_id,
                                    file_unique_id=file_unique_id,
                                    poster_url=series.poster,
                                )
                            except Exception as le:
                                log.warning("Library save failed in batch: %s", le)

                        from bot.database import db
                        if db:
                            try:
                                await db.save_file(
                                    series_slug=series.slug,
                                    series_title=series.title,
                                    quality=quality.resolution,
                                    episode_key=f"S{season}E{ep.number:02d}",
                                    file_id=file_id,
                                    file_unique_id=file_unique_id,
                                )
                                await db.log_download(
                                    user_id=user.id,
                                    series_slug=series.slug,
                                    episode=f"S{season}E{ep.number:02d}",
                                    quality=quality.resolution,
                                    file_id=file_id,
                                )
                            except Exception:
                                pass
            else:
                if bot_logger:
                    await bot_logger.log_download_error(
                        f"{series.title} S{season}E{ep.number}",
                        "Download/upload failed"
                    )

        except Exception as e:
            log.exception("Batch download error for ep %s", ep.slug)
            if bot_logger:
                await bot_logger.log_download_error(
                    f"{series.title} S{season}E{ep.number}", str(e)
                )

        # Small delay between episodes to avoid rate limits
        await asyncio.sleep(2)

    # Final summary
    try:
        await progress_msg.edit_text(
            f"{'✅' if completed == total else '⚠️'} <b>Batch complete!</b>\n"
            f"📺 {esc(series.title)} — Season {season}\n"
            f"✅ {completed}/{total} episodes uploaded.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass

    if bot_logger:
        await bot_logger.log_batch_complete(series.title, season, completed, total)


async def _do_download(client: Client, chat_id, quality, filename, title, progress_msg, user,
                       series_slug="", episode_key="", poster_url=None, is_movie=False):
    """Background task for single download."""
    try:
        success, sent_msg = await download_and_upload(
            chat_id, quality, filename, title, progress_msg, client
        )
        if success and bot_logger:
            await bot_logger.log_download_complete(title, quality.resolution, 0)

        # Save to library if upload succeeded
        if success and sent_msg and series_slug:
            file_id = None
            file_unique_id = None
            if sent_msg.video:
                file_id = sent_msg.video.file_id
                file_unique_id = sent_msg.video.file_unique_id
            elif sent_msg.document:
                file_id = sent_msg.document.file_id
                file_unique_id = sent_msg.document.file_unique_id

            if file_id and file_unique_id:
                from bot.library import library_manager
                if library_manager:
                    try:
                        await library_manager.save_to_library(
                            series_slug=series_slug,
                            series_title=slug_to_title(series_slug) if series_slug else title,
                            quality=quality.resolution,
                            episode_key=episode_key or "movie",
                            file_id=file_id,
                            file_unique_id=file_unique_id,
                            poster_url=poster_url,
                            is_movie=is_movie,
                        )
                    except Exception as le:
                        log.warning("Library save failed: %s", le)

                # Save file to DB (for duplicate prevention + library links)
                from bot.database import db
                if db:
                    try:
                        await db.save_file(
                            series_slug=series_slug,
                            series_title=slug_to_title(series_slug) if series_slug else title,
                            quality=quality.resolution,
                            episode_key=episode_key or "movie",
                            file_id=file_id,
                            file_unique_id=file_unique_id,
                        )
                        await db.log_download(
                            user_id=user.id,
                            series_slug=series_slug,
                            episode=episode_key or "movie",
                            quality=quality.resolution,
                            file_id=file_id,
                        )
                    except Exception:
                        pass

        elif not success and bot_logger:
            await bot_logger.log_download_error(title, "Download/upload failed")
    except Exception as e:
        log.exception("Download task error for %s", title)
        if bot_logger:
            await bot_logger.log_download_error(title, str(e))
        try:
            await progress_msg.edit_text(
                f"❌ <b>Error:</b> {esc(title)}\n{esc(str(e)[:200])}",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass


async def _handle_genres(q: CallbackQuery):
    cats = await api.get_categories()
    if not cats:
        await _send_text(q, "⚠️ Could not load genres.")
        return
    await _send_text(q, "📂 <b>Browse by Genre</b>", kb.genre_list(cats))


async def _handle_category(q: CallbackQuery, cat_slug: str, page: int):
    result = await api.get_category_items(cat_slug, page)
    if not result.items:
        await _send_text(q, "No items found in this category.")
        return
    title = slug_to_title(cat_slug)
    markup = kb.category_page(result.items, cat_slug, page, result.max_page)
    await _send_text(q, f"📂 <b>{esc(title)}</b> — Page {page}", markup)


# ── Quality / Server helpers ─────────────────────────────────────────

async def _populate_server_qualities(servers: list):
    """For each server with a player_url, try to resolve and populate qualities."""
    for srv in servers:
        if srv.qualities:
            continue  # Already populated
        try:
            result = await resolve_player_url(srv.player_url or srv.direct_url)
            if result:
                if result.get("qualities"):
                    srv.qualities = result["qualities"]
                elif result.get("url"):
                    vtype = result.get("type", "mp4")
                    srv.direct_url = result["url"]
                    srv.video_type = vtype
                    srv.qualities = [Quality(
                        resolution="auto",
                        url=result["url"],
                        label=f"Auto ({vtype.upper()})"
                    )]
        except Exception as e:
            log.debug("Quality populate failed for %s: %s", srv.name, e)


def _pick_quality(srv, quality_idx: int):
    """Pick a quality from a server by index, with fallback."""
    if srv.qualities and quality_idx < len(srv.qualities):
        return srv.qualities[quality_idx]
    if srv.qualities:
        return srv.qualities[0]
    if srv.direct_url:
        return Quality(resolution="auto", url=srv.direct_url)
    return None


def _find_quality_match(servers: list, quality_pref: str) -> Quality | None:
    """Find the best quality matching preference across all servers."""
    all_qualities = []
    for srv in servers:
        all_qualities.extend(srv.qualities)

    if not all_qualities:
        # Fallback to direct URLs
        for srv in servers:
            if srv.direct_url:
                return Quality(resolution="auto", url=srv.direct_url)
        return None

    if quality_pref == "auto":
        return all_qualities[0]  # Highest quality (sorted by bandwidth desc)

    # Try exact match
    for q in all_qualities:
        if q.resolution == quality_pref:
            return q

    # Try closest match
    pref_height = int(quality_pref.replace("p", "")) if quality_pref.endswith("p") else 0
    best = None
    best_diff = float("inf")
    for q in all_qualities:
        try:
            h = int(q.resolution.replace("p", ""))
            diff = abs(h - pref_height)
            if diff < best_diff:
                best_diff = diff
                best = q
        except ValueError:
            continue

    return best or all_qualities[0]


# ── Server data cache (in-memory, per chat) ──────────────────────────

_server_cache: dict[str, dict] = {}


def _store_servers(chat_id: int, key: str, servers: list, title: str = ""):
    """Store resolved servers for download callbacks."""
    cache_key = f"{chat_id}:{key}"
    _server_cache[cache_key] = {
        "servers": servers,
        "title": title,
    }
    # Keep cache bounded
    if len(_server_cache) > 200:
        # Remove oldest entries
        keys = list(_server_cache.keys())
        for k in keys[:50]:
            _server_cache.pop(k, None)


def _get_servers(chat_id: int, key: str) -> dict | None:
    cache_key = f"{chat_id}:{key}"
    return _server_cache.get(cache_key)


# ── Utilities ─────────────────────────────────────────────────────

async def _send_text(q: CallbackQuery, text: str, markup=None):
    try:
        await q.edit_message_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception:
        try:
            await q.edit_message_caption(caption=text[:1024], parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            await q.message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)


async def _safe_edit(q: CallbackQuery, text: str, markup=None):
    try:
        await q.edit_message_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception:
        await q.message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
