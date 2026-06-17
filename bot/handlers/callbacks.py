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
import bot.logger
from bot.downloader import (
    download_and_upload, make_episode_filename, make_movie_filename,
    sanitize_filename,
)
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
            # dl:quality:ep_slug — auto server selection
            parts = data.split(":", 2)
            await _handle_download(
                client, query,
                quality_pref=parts[1],
                ep_slug=parts[2],
            )

        elif data.startswith("mdl:"):
            # mdl:quality:movie_slug — auto server selection
            parts = data.split(":", 2)
            await _handle_movie_download(
                client, query,
                quality_pref=parts[1],
                movie_slug=parts[2],
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
        if bot.logger.bot_logger:
            await bot.logger.bot_logger.log_error(f"callback:{data}", str(e))
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

    # Cache poster for later use in library
    if series.poster:
        _poster_cache[series.slug] = series.poster

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

    # Skeleton: show default quality buttons instantly, resolve on download
    from config.settings import settings
    default_qualities = set(settings.site.default_qualities)

    text = f"🎬 <b>{esc(movie.title)}</b>\n\n"
    if movie.genres:
        text += f"🏷 {', '.join(movie.genres[:6])}\n"
    if movie.description:
        text += f"\n{esc(truncate(movie.description, 350))}\n"
    text += "\n📊 <b>Select quality to download:</b>"

    # Store raw servers — will be resolved on download
    _store_servers(q.message.chat.id, f"movie:{slug}", movie.servers, movie.title)

    markup = kb.quality_picker(default_qualities, slug, "mp:1", is_movie=True)

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
    """Show episode with skeleton quality buttons instantly — no server resolution."""
    episode = await api.get_episode(ep_slug)

    # Show default quality buttons immediately (like YouTube skeleton)
    from config.settings import settings
    default_qualities = set(settings.site.default_qualities)  # ["480p", "720p", "1080p"]

    text = f"▶️ <b>{esc(episode.title)}</b>\n\n"
    text += "📊 <b>Select quality to download:</b>"

    series_slug = extract_series_slug(ep_slug)
    back_cb = f"sr:{short_slug(series_slug)}" if series_slug else "rp:1"

    # Store raw (unresolved) servers — will be resolved on download
    _store_servers(q.message.chat.id, ep_slug, episode.servers, episode.title)

    markup = kb.quality_picker(default_qualities, ep_slug, back_cb, is_movie=False)

    try:
        await q.edit_message_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception:
        try:
            await q.edit_message_caption(caption=text[:1024], parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            await q.message.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)


async def _handle_download(client: Client, q: CallbackQuery, quality_pref: str, ep_slug: str):
    """Handle single episode download — resolves VidStream only, fallback if broken."""
    chat_id = q.message.chat.id
    user = q.from_user

    # Get stored server data (may be raw/unresolved from skeleton episode view)
    data = _get_servers(chat_id, ep_slug)
    if not data:
        episode = await api.get_episode(ep_slug)
        _store_servers(chat_id, ep_slug, episode.servers, episode.title)
        data = _get_servers(chat_id, ep_slug)

    if not data or not data["servers"]:
        await _safe_edit(q, "⚠️ No servers found. Please go back and try again.")
        return

    title = data.get("title", ep_slug)
    raw_servers = data["servers"]

    # Lazy resolve: VidStream first, fallback only if it fails
    resolved = await _lazy_resolve_servers(raw_servers)
    if resolved:
        _store_servers(chat_id, ep_slug, resolved, title)

    quality = _find_quality_match(resolved or raw_servers, quality_pref)

    if not quality:
        await _safe_edit(q, "⚠️ No downloadable URL found on any server.")
        return

    # Warn user if quality doesn't match what they requested
    if quality.resolution != quality_pref and quality.resolution != "auto":
        log.info("Quality fallback: requested %s, got %s", quality_pref, quality.resolution)

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
                await q.message.reply_document(
                    document=cached_fid,
                    file_name=filename,
                    caption=f"📦 <b>{esc(title)}</b> [{quality.resolution}]\n<i>⚡ From library — instant delivery!</i>",
                    parse_mode=enums.ParseMode.HTML,
                )
                return
            except Exception as e:
                log.warning("Cached file expired/deleted, removing from DB and re-downloading: %s", e)
                # Remove invalid cache entry so it gets re-downloaded
                await db.files.delete_one({
                    "series_slug": series_slug,
                    "quality": quality.resolution,
                    "episode_key": episode_key,
                })

    # Get poster from cache — if not cached, fetch from API
    poster_url = _poster_cache.get(series_slug, "") if series_slug else ""
    if not poster_url and series_slug:
        try:
            series_data = await api.get_series(series_slug)
            if series_data and series_data.poster:
                poster_url = series_data.poster
                _poster_cache[series_slug] = poster_url
        except Exception:
            pass

    # Log
    if bot.logger.bot_logger:
        await bot.logger.bot_logger.log_download_start(
            user.id, user.username or str(user.id), title, quality.resolution
        )

    # Send progress message — show actual quality (may differ from requested)
    quality_label = quality.resolution
    if quality.resolution != quality_pref and quality.resolution != quality_pref:
        quality_label = f"{quality.resolution} (requested {quality_pref})"

    progress_msg = await q.message.reply_text(
        f"📥 <b>Starting download:</b> {esc(title)} [{quality_label}]",
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


async def _handle_movie_download(client: Client, q: CallbackQuery, quality_pref: str, movie_slug: str):
    """Handle movie download — resolves VidStream only, fallback if broken."""
    chat_id = q.message.chat.id
    user = q.from_user

    data = _get_servers(chat_id, f"movie:{movie_slug}")
    if not data:
        movie = await api.get_movie(movie_slug)
        _store_servers(chat_id, f"movie:{movie_slug}", movie.servers, movie.title)
        data = _get_servers(chat_id, f"movie:{movie_slug}")

    if not data or not data["servers"]:
        await _safe_edit(q, "⚠️ No servers found. Please go back and try again.")
        return

    title = data.get("title", slug_to_title(movie_slug))
    raw_servers = data["servers"]

    resolved = await _lazy_resolve_servers(raw_servers)
    if resolved:
        _store_servers(chat_id, f"movie:{movie_slug}", resolved, title)

    quality = _find_quality_match(resolved or raw_servers, quality_pref)

    if not quality:
        await _safe_edit(q, "⚠️ No downloadable URL found on any server.")
        return

    filename = make_movie_filename(title, quality.resolution)

    # ── Duplicate check for movies ──
    from bot.database import db
    if db:
        cached_fid = await db.get_cached_file(movie_slug, quality.resolution, "movie")
        if cached_fid:
            try:
                await q.message.reply_document(
                    document=cached_fid,
                    file_name=filename,
                    caption=f"📦 <b>{esc(title)}</b> [{quality.resolution}]\n<i>⚡ From library — instant delivery!</i>",
                    parse_mode=enums.ParseMode.HTML,
                )
                return
            except Exception as e:
                log.warning("Cached movie file expired/deleted, removing from DB: %s", e)
                await db.files.delete_one({
                    "series_slug": movie_slug,
                    "quality": quality.resolution,
                    "episode_key": "movie",
                })

    if bot.logger.bot_logger:
        await bot.logger.bot_logger.log_download_start(
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

    if bot.logger.bot_logger:
        await bot.logger.bot_logger.log_batch_start(
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
    skipped = 0  # Episodes served from cache
    sent_messages = [progress_msg]  # Track all messages for auto-delete

    for i, ep in enumerate(episodes, 1):
        try:
            cache_info = f"\n⚡ {skipped} from library" if skipped > 0 else ""
            await progress_msg.edit_text(
                f"📦 <b>Batch Download</b> — {esc(series.title)} S{season}\n\n"
                f"📥 Processing S{season}E{ep.number}... ({i}/{total})\n"
                f"✅ {completed} completed{cache_info}",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

        try:
            # ── Check cache first — skip already downloaded episodes ──
            ep_key = f"S{season}E{ep.number:02d}"
            from bot.database import db
            if db:
                cached_fid = await db.get_cached_file(series.slug, quality_pref, ep_key)
                if cached_fid:
                    try:
                        await client.send_document(
                            chat_id,
                            document=cached_fid,
                            file_name=make_episode_filename(series.title, season, ep.number, quality_pref),
                            caption=f"📦 <b>{esc(series.title)} {ep_key}</b> [{quality_pref}]\n<i>⚡ From library — instant!</i>",
                            parse_mode=enums.ParseMode.HTML,
                        )
                        completed += 1
                        skipped += 1
                        continue
                    except Exception:
                        # Expired file — remove from cache
                        await db.files.delete_one({
                            "series_slug": series.slug,
                            "quality": quality_pref,
                            "episode_key": ep_key,
                        })

            # Resolve episode (lazy — VidStream first)
            episode = await api.get_episode(ep.slug)
            resolved = await _lazy_resolve_servers(episode.servers)

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
            sent_messages.append(ep_msg)

            success, sent_msg = await download_and_upload(
                chat_id, quality.master_url or quality.url, quality.resolution, filename,
                f"{series.title} S{season}E{ep.number}",
                ep_msg, client, variant_url=quality.url,
            )

            if success:
                completed += 1
                if bot.logger.bot_logger:
                    await bot.logger.bot_logger.log_download_complete(
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
                if bot.logger.bot_logger:
                    await bot.logger.bot_logger.log_download_error(
                        f"{series.title} S{season}E{ep.number}",
                        "Download/upload failed"
                    )

        except Exception as e:
            log.exception("Batch download error for ep %s", ep.slug)
            if bot.logger.bot_logger:
                await bot.logger.bot_logger.log_download_error(
                    f"{series.title} S{season}E{ep.number}", str(e)
                )

        # Small delay between episodes to avoid rate limits
        await asyncio.sleep(2)

    # Final summary
    cache_info = f"\n⚡ {skipped} served from library (instant)" if skipped > 0 else ""
    downloaded = completed - skipped
    try:
        await progress_msg.edit_text(
            f"{'✅' if completed == total else '⚠️'} <b>Batch Complete!</b>\n"
            f"┌ 📺 {esc(series.title)} — Season {season}\n"
            f"├ ✅ {completed}/{total} episodes delivered\n"
            f"{'├ 📥 ' + str(downloaded) + ' freshly downloaded' + chr(10) if downloaded > 0 else ''}"
            f"{'├ ⚡ ' + str(skipped) + ' from library (instant)' + chr(10) if skipped > 0 else ''}"
            f"└ 🗑️ This message will auto-delete in 12h",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass

    if bot.logger.bot_logger:
        await bot.logger.bot_logger.log_batch_complete(series.title, season, completed, total)

    # Auto-delete all bot messages after 12 hours (43200 seconds)
    asyncio.create_task(_auto_delete_messages(client, chat_id, sent_messages, 43200))


async def _do_download(client: Client, chat_id, quality, filename, title, progress_msg, user,
                       series_slug="", episode_key="", poster_url=None, is_movie=False):
    """Background task for single download using N_m3u8DL-RE."""
    try:
        # quality.url is the direct m3u8/mp4 stream URL (from resolver)
        success, sent_msg = await download_and_upload(
            chat_id, quality.master_url or quality.url, quality.resolution, filename, title, progress_msg, client,
            variant_url=quality.url,
        )
        if success and bot.logger.bot_logger:
            await bot.logger.bot_logger.log_download_complete(title, quality.resolution, 0)

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

        elif not success and bot.logger.bot_logger:
            await bot.logger.bot_logger.log_download_error(title, "Download/upload failed")

        # Auto-delete progress + file messages after 12h
        to_delete = [progress_msg]
        if sent_msg:
            to_delete.append(sent_msg)
        asyncio.create_task(_auto_delete_messages(client, chat_id, to_delete, 43200))

    except Exception as e:
        log.exception("Download task error for %s", title)
        if bot.logger.bot_logger:
            await bot.logger.bot_logger.log_download_error(title, str(e))
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


def _sort_qualities(qualities: set[str]) -> list[str]:
    """Sort quality strings like 360p, 480p, 720p, 1080p."""
    order = {"360p": 1, "480p": 2, "720p": 3, "1080p": 4, "auto": 5}
    return sorted(qualities, key=lambda q: order.get(q, 99))


async def _lazy_resolve_servers(servers: list) -> list:
    """Resolve servers lazily: VidStream ONLY, fallback to next only if it fails.
    
    Like YouTube — show buttons instantly, resolve the stream only when
    the user actually clicks download. Try VidStream first since it always
    has all qualities (480p/720p/1080p). Only try HydraX/Vidmoly if
    VidStream completely fails.
    
    Returns the server list with the resolved server populated.
    """
    from config.settings import settings
    preferred = settings.site.preferred_servers

    def _server_priority(srv):
        name_lower = srv.name.lower()
        for i, pref in enumerate(preferred):
            if pref.lower() in name_lower:
                return i
        return len(preferred)

    # First resolve the raw servers (get player URLs)
    sorted_servers = sorted(servers, key=_server_priority)
    
    # Check if any server is already resolved with qualities
    for srv in sorted_servers:
        if srv.qualities and any(q.resolution != "auto" for q in srv.qualities):
            return sorted_servers

    # Need to resolve: first get player URLs if not already done
    needs_resolve = [s for s in sorted_servers if not s.player_url and not s.direct_url]
    if needs_resolve:
        resolved_list = await api.resolve_all_servers(servers)
        sorted_servers = sorted(resolved_list, key=_server_priority)

    # Now try to extract stream from each server in priority order
    await _populate_server_qualities(sorted_servers)
    return sorted_servers


async def _populate_server_qualities(servers: list):
    """Extract stream URLs and qualities from servers in priority order.
    
    Stops at the FIRST server that works. No scanning all servers.
    """
    from extractors.resolver import resolve_player_url
    from config.settings import settings
    preferred = settings.site.preferred_servers

    def _server_priority(srv):
        name_lower = srv.name.lower()
        for i, pref in enumerate(preferred):
            if pref.lower() in name_lower:
                return i
        return len(preferred)

    sorted_servers = sorted(servers, key=_server_priority)

    for srv in sorted_servers:
        if srv.qualities and any(q.resolution != "auto" for q in srv.qualities):
            return  # Already resolved
        url = srv.player_url or srv.direct_url
        if not url:
            continue

        try:
            result = await resolve_player_url(url)
            if result:
                if result.get("qualities"):
                    srv.qualities = result["qualities"]
                    log.info("✅ %s: %d qualities (%s)",
                             srv.name, len(srv.qualities),
                             ", ".join(q.resolution for q in srv.qualities))
                    return  # Done — don't check others
                elif result.get("url"):
                    vtype = result.get("type", "mp4")
                    srv.direct_url = result["url"]
                    srv.video_type = vtype
                    srv.qualities = [Quality(
                        resolution="auto",
                        url=result["url"],
                        label=f"Auto ({vtype.upper()})"
                    )]
                    log.info("✅ %s: auto quality", srv.name)
                    return
        except Exception as e:
            log.debug("Resolver failed for %s: %s", srv.name, e)

        log.info("❌ %s: failed, trying next...", srv.name)


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
    """Find the best quality matching preference.
    
    Uses whichever server was resolved by _populate_server_qualities
    (VidStream first, fallback to HydraX/Vidmoly).
    """
    from config.settings import settings
    preferred = settings.site.preferred_servers

    # Sort servers by priority
    def _server_priority(srv):
        name_lower = srv.name.lower()
        for i, pref in enumerate(preferred):
            if pref.lower() in name_lower:
                return i
        return len(preferred)

    sorted_servers = sorted(servers, key=_server_priority)

    if quality_pref == "auto":
        for srv in sorted_servers:
            if srv.qualities:
                return srv.qualities[0]
        for srv in sorted_servers:
            if srv.direct_url:
                return Quality(resolution="auto", url=srv.direct_url)
        return None

    # Pass 1: exact match across ALL servers (priority order)
    for srv in sorted_servers:
        for q in srv.qualities:
            if q.resolution == quality_pref:
                log.info("Exact quality match: %s on server %s", quality_pref, srv.name)
                return q

    # Pass 2: closest numeric match across ALL servers
    pref_height = int(quality_pref.replace("p", "")) if quality_pref.endswith("p") else 0
    best = None
    best_diff = float("inf")
    best_server = ""
    for srv in sorted_servers:
        for q in srv.qualities:
            try:
                h = int(q.resolution.replace("p", ""))
                diff = abs(h - pref_height)
                if diff < best_diff:
                    best_diff = diff
                    best = q
                    best_server = srv.name
            except ValueError:
                continue

    if best:
        log.warning("Quality %s not found. Closest match: %s on server %s",
                     quality_pref, best.resolution, best_server)
        return best

    # Pass 3: any "auto" quality
    for srv in sorted_servers:
        for q in srv.qualities:
            if q.resolution == "auto":
                log.warning("Quality %s not found. Falling back to 'auto' on server %s",
                             quality_pref, srv.name)
                return q

    # Last resort: any direct URL
    for srv in sorted_servers:
        if srv.direct_url:
            return Quality(resolution="auto", url=srv.direct_url)
    return None


# ── Server data cache (in-memory, per chat) ──────────────────────────

_server_cache: dict[str, dict] = {}

# ── Poster cache (series_slug -> poster_url) ──────────────────────────

_poster_cache: dict[str, str] = {}


def _store_servers(chat_id: int, key: str, servers: list, title: str = "", poster_url: str = ""):
    """Store resolved servers for download callbacks."""
    cache_key = f"{chat_id}:{key}"
    _server_cache[cache_key] = {
        "servers": servers,
        "title": title,
        "poster_url": poster_url,
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


async def _auto_delete_messages(client: Client, chat_id: int, messages: list, delay_seconds: int = 43200):
    """Auto-delete a list of bot messages after delay (default 12h)."""
    await asyncio.sleep(delay_seconds)
    for msg in messages:
        try:
            await client.delete_messages(chat_id, msg.id)
        except Exception:
            pass
