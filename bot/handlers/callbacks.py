"""Callback query router — handles all inline button presses."""

from __future__ import annotations
import logging

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from api.client import api
from bot import keyboards as kb
from bot.auth import require_approved
from bot.logger import bot_logger
from utils.helpers import esc, truncate, short_slug, extract_series_slug, slug_to_title

log = logging.getLogger(__name__)


@require_approved
async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    try:
        if data == "m:main":
            await _send_text(q, "🎌 <b>AnimeDekho Bot</b>\n\nChoose an option:", kb.main_menu())

        elif data == "m:genres":
            await _handle_genres(q)

        elif data.startswith("rp:"):
            await _handle_series_listing(q, int(data.split(":")[1]))

        elif data.startswith("mp:"):
            await _handle_movies_listing(q, int(data.split(":")[1]))

        elif data.startswith("sr:"):
            await _handle_series_detail(q, data[3:])

        elif data.startswith("mr:"):
            await _handle_movie_detail(q, data[3:])

        elif data.startswith("se:"):
            parts = data.split(":")
            await _handle_season(q, slug=parts[1], season=int(parts[2]))

        elif data.startswith("ep:"):
            await _handle_episode(q, data[3:])

        elif data.startswith("ct:"):
            parts = data.split(":")
            await _handle_category(q, cat_slug=parts[1], page=int(parts[2]))

    except Exception as e:
        log.exception("Callback error for %s", data)
        if bot_logger:
            await bot_logger.log_error(f"callback:{data}", str(e))
        await _safe_edit(q, f"⚠️ Error: {esc(str(e)[:200])}\n\nTry /start")


# ── Handler implementations ───────────────────────────────────────

async def _handle_series_listing(q, page: int):
    result = await api.get_recent_series(page)
    if not result.items:
        await _send_text(q, "No series found.")
        return
    markup = kb.listing_page(result.items, page, result.max_page, "rp", "sr", "📺")
    await _send_text(q, f"📺 <b>Recent Series</b> — Page {page}/{result.max_page}", markup)


async def _handle_movies_listing(q, page: int):
    result = await api.get_recent_movies(page)
    if not result.items:
        await _send_text(q, "No movies found.")
        return
    markup = kb.listing_page(result.items, page, result.max_page, "mp", "mr", "🎬")
    await _send_text(q, f"🎬 <b>Movies</b> — Page {page}/{result.max_page}", markup)


async def _handle_series_detail(q, slug: str):
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
        await q.message.chat.send_photo(
            photo=series.poster,
            caption=text[:1024],
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
    else:
        await _send_text(q, text, markup)


async def _handle_movie_detail(q, slug: str):
    movie = await api.get_movie(slug)

    # Resolve servers
    resolved = await api.resolve_all_servers(movie.servers)
    movie.servers = resolved

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

    markup = kb.movie_detail(movie)

    if movie.poster:
        try:
            await q.message.delete()
        except Exception:
            pass
        await q.message.chat.send_photo(
            photo=movie.poster,
            caption=text[:1024],
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
    else:
        await _send_text(q, text, markup)


async def _handle_season(q, slug: str, season: int):
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
        await q.edit_message_caption(caption=text[:1024], parse_mode=ParseMode.HTML, reply_markup=markup)
    except Exception:
        await _safe_edit(q, text, markup)


async def _handle_episode(q, ep_slug: str):
    episode = await api.get_episode(ep_slug)

    # Resolve all servers
    resolved = await api.resolve_all_servers(episode.servers)

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
            if srv.player_url:
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

    markup = kb.server_picker(resolved, ep_slug, back_cb)

    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except Exception:
        try:
            await q.edit_message_caption(caption=text[:1024], parse_mode=ParseMode.HTML, reply_markup=markup)
        except Exception:
            await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def _handle_genres(q):
    cats = await api.get_categories()
    if not cats:
        await _send_text(q, "⚠️ Could not load genres.")
        return
    await _send_text(q, "📂 <b>Browse by Genre</b>", kb.genre_list(cats))


async def _handle_category(q, cat_slug: str, page: int):
    result = await api.get_category_items(cat_slug, page)
    if not result.items:
        await _send_text(q, "No items found in this category.")
        return
    title = slug_to_title(cat_slug)
    markup = kb.category_page(result.items, cat_slug, page, result.max_page)
    await _send_text(q, f"📂 <b>{esc(title)}</b> — Page {page}", markup)


# ── Utilities ─────────────────────────────────────────────────────

async def _send_text(q, text: str, markup=None):
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except Exception:
        try:
            await q.edit_message_caption(caption=text[:1024], parse_mode=ParseMode.HTML, reply_markup=markup)
        except Exception:
            await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def _safe_edit(q, text: str, markup=None):
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except Exception:
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
