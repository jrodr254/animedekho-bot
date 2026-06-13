"""Inline keyboard builders — keeps handlers clean."""

from __future__ import annotations
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from api.models import (
    SearchResult, Series, Season, Episode, VideoServer,
    Category, PaginatedResult, Quality,
)
from config.settings import settings
from utils.helpers import short_slug

S = settings.bot


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📺 Recent Series", callback_data="rp:1"),
            InlineKeyboardButton("🎬 Movies", callback_data="mp:1"),
        ],
        [InlineKeyboardButton("📂 Browse Genres", callback_data="m:genres")],
    ])


def search_results(results: list[SearchResult]) -> InlineKeyboardMarkup:
    buttons = []
    for r in results[: S.max_search_results]:
        prefix = "sr" if r.is_series else "mr"
        emoji = "📺" if r.is_series else "🎬"
        cb = f"{prefix}:{short_slug(r.slug)}"
        buttons.append([InlineKeyboardButton(f"{emoji} {r.title[:45]}", callback_data=cb)])
    buttons.append([_menu_btn()])
    return InlineKeyboardMarkup(buttons)


def listing_page(
    items: list[SearchResult],
    page: int,
    max_page: int,
    prefix: str,      # "rp" or "mp"
    item_prefix: str,  # "sr" or "mr"
    emoji: str,
) -> InlineKeyboardMarkup:
    buttons = []
    for it in items[: S.items_per_page]:
        cb = f"{item_prefix}:{short_slug(it.slug)}"
        buttons.append([InlineKeyboardButton(f"{emoji} {it.title[:45]}", callback_data=cb)])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{prefix}:{page - 1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"{prefix}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([_menu_btn()])
    return InlineKeyboardMarkup(buttons)


def season_picker(series: Series) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for sn in sorted(series.seasons.keys()):
        ep_count = series.seasons[sn].episode_count
        cb = f"se:{short_slug(series.slug, 35)}:{sn}"
        row.append(InlineKeyboardButton(f"S{sn} ({ep_count}ep)", callback_data=cb))
        if len(row) >= S.seasons_per_row:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # Batch download buttons per season
    for sn in sorted(series.seasons.keys()):
        cb = f"bat:{short_slug(series.slug, 30)}:{sn}"
        buttons.append([InlineKeyboardButton(
            f"📥 Batch Download S{sn}", callback_data=cb
        )])
    buttons.append([
        InlineKeyboardButton("🔙 Back", callback_data="rp:1"),
        _menu_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def episode_picker(
    series_slug: str,
    season: int,
    episodes: list[Episode],
) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for ep in episodes:
        cb = f"ep:{short_slug(ep.slug)}"
        row.append(InlineKeyboardButton(f"Ep {ep.number}", callback_data=cb))
        if len(row) >= S.episodes_per_row:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # Batch download for this season
    cb = f"bat:{short_slug(series_slug, 30)}:{season}"
    buttons.append([InlineKeyboardButton(
        f"📥 Batch Download S{season}", callback_data=cb
    )])
    buttons.append([
        InlineKeyboardButton("🔙 Seasons", callback_data=f"sr:{short_slug(series_slug)}"),
        _menu_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def server_picker(
    servers: list[VideoServer],
    ep_slug: str,
    back_cb: str,
) -> InlineKeyboardMarkup:
    """
    Show servers with download buttons.
    Each server shows available quality options as download buttons.
    Falls back to URL buttons for servers without direct URLs.
    """
    buttons = []

    for si, srv in enumerate(servers):
        if srv.qualities:
            # Show each quality as a download button
            for qi, q in enumerate(srv.qualities[:4]):  # max 4 qualities per server
                cb = f"dl:{si}:{qi}:{short_slug(ep_slug, 40)}"
                label = f"📥 {srv.name} - {q.label}"
                buttons.append([InlineKeyboardButton(label[:40], callback_data=cb)])
        elif srv.direct_url:
            # Single download button for direct URL
            cb = f"dl:{si}:0:{short_slug(ep_slug, 40)}"
            label = f"📥 {srv.name} ({srv.video_type.upper()})"
            buttons.append([InlineKeyboardButton(label[:40], callback_data=cb)])
        elif srv.player_url:
            # Fallback: URL button for player embed
            label = f"▶️ {srv.name}"
            buttons.append([InlineKeyboardButton(label, url=srv.player_url)])

    # Always provide a "Watch on Site" link
    buttons.append([InlineKeyboardButton(
        "🌐 Watch on Site",
        url=f"https://animedekho.app/epi/{ep_slug}/",
    )])
    buttons.append([
        InlineKeyboardButton("🔙 Back", callback_data=back_cb),
        _menu_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def movie_server_picker(
    servers: list[VideoServer],
    movie_slug: str,
    back_cb: str = "mp:1",
) -> InlineKeyboardMarkup:
    """Movie server picker with download buttons."""
    buttons = []

    for si, srv in enumerate(servers[:5]):
        if srv.qualities:
            for qi, q in enumerate(srv.qualities[:4]):
                cb = f"mdl:{si}:{qi}:{short_slug(movie_slug, 38)}"
                label = f"📥 {srv.name} - {q.label}"
                buttons.append([InlineKeyboardButton(label[:40], callback_data=cb)])
        elif srv.direct_url:
            cb = f"mdl:{si}:0:{short_slug(movie_slug, 38)}"
            label = f"📥 {srv.name} ({srv.video_type.upper()})"
            buttons.append([InlineKeyboardButton(label[:40], callback_data=cb)])
        elif srv.player_url:
            buttons.append([InlineKeyboardButton(f"▶️ {srv.name}", url=srv.player_url)])

    buttons.append([InlineKeyboardButton("🌐 Watch on Site", url=f"https://animedekho.app/movie-hindi/{movie_slug}/")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=back_cb), _menu_btn()])
    return InlineKeyboardMarkup(buttons)


def batch_quality_picker(series_slug: str, season: int) -> InlineKeyboardMarkup:
    """Quality selection for batch download."""
    buttons = []
    ss = short_slug(series_slug, 28)
    for q in ["360p", "480p", "720p", "1080p", "auto"]:
        cb = f"bq:{ss}:{season}:{q}"
        buttons.append([InlineKeyboardButton(f"📥 {q}", callback_data=cb)])
    buttons.append([
        InlineKeyboardButton("🔙 Cancel", callback_data=f"se:{short_slug(series_slug, 35)}:{season}"),
        _menu_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def genre_list(categories: list[Category]) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for c in categories:
        cb = f"ct:{short_slug(c.slug, 50)}:1"
        row.append(InlineKeyboardButton(f"{c.name} ({c.count})", callback_data=cb))
        if len(row) >= 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([_menu_btn()])
    return InlineKeyboardMarkup(buttons)


def category_page(
    items: list[SearchResult],
    cat_slug: str,
    page: int,
    max_page: int,
) -> InlineKeyboardMarkup:
    buttons = []
    for it in items[: S.items_per_page]:
        prefix = "sr" if it.is_series else "mr"
        emoji = "📺" if it.is_series else "🎬"
        cb = f"{prefix}:{short_slug(it.slug)}"
        buttons.append([InlineKeyboardButton(f"{emoji} {it.title[:45]}", callback_data=cb)])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"ct:{short_slug(cat_slug, 50)}:{page - 1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"ct:{short_slug(cat_slug, 50)}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([
        InlineKeyboardButton("🔙 Genres", callback_data="m:genres"),
        _menu_btn(),
    ])
    return InlineKeyboardMarkup(buttons)


def movie_detail(movie, back_cb: str = "mp:1") -> InlineKeyboardMarkup:
    """Movie detail with download buttons."""
    return movie_server_picker(movie.servers, movie.slug, back_cb)


# ── Helpers ───────────────────────────────────────────────────────

def _menu_btn() -> InlineKeyboardButton:
    return InlineKeyboardButton("🏠 Menu", callback_data="m:main")
