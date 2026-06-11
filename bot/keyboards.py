"""Inline keyboard builders — keeps handlers clean."""

from __future__ import annotations
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from api.models import (
    SearchResult, Series, Season, Episode, VideoServer,
    Category, PaginatedResult,
)
from config.settings import settings
from utils.helpers import short_slug

S = settings.bot


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Search Anime", callback_data="m:search")],
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
    buttons = []
    for i, srv in enumerate(servers):
        if srv.player_url:
            label = f"▶️ {srv.name}"
            buttons.append([InlineKeyboardButton(label, url=srv.player_url)])
        elif srv.direct_url:
            label = f"⬇️ {srv.name} ({srv.video_type.upper()})"
            buttons.append([InlineKeyboardButton(label, url=srv.direct_url)])

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
    buttons = []
    for srv in movie.servers[:5]:
        if srv.player_url:
            buttons.append([InlineKeyboardButton(f"▶️ {srv.name}", url=srv.player_url)])
        elif srv.direct_url:
            buttons.append([InlineKeyboardButton(f"⬇️ {srv.name}", url=srv.direct_url)])
    buttons.append([InlineKeyboardButton("🌐 Watch on Site", url=movie.url)])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=back_cb), _menu_btn()])
    return InlineKeyboardMarkup(buttons)


# ── Helpers ───────────────────────────────────────────────────────

def _menu_btn() -> InlineKeyboardButton:
    return InlineKeyboardButton("🏠 Menu", callback_data="m:main")
