"""High-level async API client for AnimeDekho."""

from __future__ import annotations
import json
import logging
import re
from urllib.parse import quote

from config.settings import settings
from utils.http import http_client
from .models import (
    SearchResult, Series, Movie, Episode, Category,
    PaginatedResult, VideoServer,
)
from .parser import (
    parse_nonce, parse_search_html, parse_listing_page,
    parse_series_detail, parse_episode_page, parse_movie_page,
    parse_categories, RE_SERIES_URL, RE_MOVIE_URL,
)

log = logging.getLogger(__name__)
cfg = settings.site


class AnimeDekhoAPI:
    """All site interactions go through here."""

    def __init__(self):
        self._nonce: str | None = None

    # ── Nonce management ──────────────────────────────────────────

    async def _ensure_nonce(self) -> str:
        if self._nonce:
            return self._nonce
        html = await http_client.get(f"{cfg.base_url}/home/", ttl=600)
        self._nonce = parse_nonce(html)
        if not self._nonce:
            raise RuntimeError("Failed to extract nonce from site")
        log.info("Nonce acquired: %s", self._nonce[:6] + "...")
        return self._nonce

    def _invalidate_nonce(self):
        self._nonce = None

    # ── Search ────────────────────────────────────────────────────

    async def search(self, query: str, page: int = 1) -> list[SearchResult]:
        """Search anime/movies via admin-ajax action_search."""
        nonce = await self._ensure_nonce()
        vars_data = json.dumps({
            "_wpsearch": nonce,
            "search": query,
            "page": page,
        })
        try:
            resp = await http_client.post(
                cfg.ajax_url,
                data={"action": "action_search", "vars": vars_data},
                ttl=settings.cache.search_ttl,
            )
        except Exception:
            # Nonce might have expired
            self._invalidate_nonce()
            nonce = await self._ensure_nonce()
            vars_data = json.dumps({
                "_wpsearch": nonce,
                "search": query,
                "page": page,
            })
            resp = await http_client.post(
                cfg.ajax_url,
                data={"action": "action_search", "vars": vars_data},
                ttl=settings.cache.search_ttl,
            )

        try:
            data = json.loads(resp)
            html = data.get("html", resp)
        except (json.JSONDecodeError, AttributeError):
            html = resp

        return parse_search_html(html)

    # ── Listings ──────────────────────────────────────────────────

    async def get_recent_series(self, page: int = 1) -> PaginatedResult:
        url = f"{cfg.base_url}{cfg.series_path}/"
        if page > 1:
            url = f"{cfg.base_url}{cfg.series_path}/page/{page}/"
        html = await http_client.get(url, ttl=settings.cache.listing_ttl)
        result = parse_listing_page(html, RE_SERIES_URL, "series")
        result.current_page = page
        return result

    async def get_recent_movies(self, page: int = 1) -> PaginatedResult:
        url = f"{cfg.base_url}{cfg.movies_path}/"
        if page > 1:
            url = f"{cfg.base_url}{cfg.movies_path}/page/{page}/"
        html = await http_client.get(url, ttl=settings.cache.listing_ttl)
        result = parse_listing_page(html, RE_MOVIE_URL, "movie")
        result.current_page = page
        return result

    # ── Detail pages ──────────────────────────────────────────────

    async def get_series(self, slug: str) -> Series:
        url = f"{cfg.base_url}{cfg.series_path}/{slug}/"
        html = await http_client.get(url)
        return parse_series_detail(html, slug)

    async def get_episode(self, ep_slug: str) -> Episode:
        url = f"{cfg.base_url}{cfg.episode_path}/{ep_slug}/"
        html = await http_client.get(url)
        return parse_episode_page(html, ep_slug)

    async def get_movie(self, slug: str) -> Movie:
        url = f"{cfg.base_url}/movie-hindi/{slug}/"
        html = await http_client.get(url)
        return parse_movie_page(html, slug)

    # ── Video server resolution ───────────────────────────────────

    async def resolve_server(self, server: VideoServer) -> VideoServer:
        """Fetch proxy URL and extract real player URL."""
        if not server.proxy_url:
            return server
        try:
            html = await http_client.get(server.proxy_url, ttl=120)
            m = re.search(r'<iframe[^>]*src="([^"]+)"', html)
            if m:
                server.player_url = m.group(1)
            else:
                # Some servers return a direct redirect or script-based URL
                m2 = re.search(r'(?:src|url|file)\s*[=:]\s*["\']?(https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*)', html)
                if m2:
                    server.direct_url = m2.group(1)
                    server.video_type = "m3u8" if ".m3u8" in m2.group(1) else "mp4"
        except Exception as e:
            log.warning("Failed to resolve server %s: %s", server.name, e)
        return server

    async def resolve_all_servers(self, servers: list[VideoServer]) -> list[VideoServer]:
        """Resolve all servers, return only available ones."""
        import asyncio
        tasks = [self.resolve_server(s) for s in servers]
        resolved = await asyncio.gather(*tasks, return_exceptions=True)
        return [s for s in resolved if isinstance(s, VideoServer) and s.is_available]

    # ── Categories ────────────────────────────────────────────────

    async def get_categories(self) -> list[Category]:
        url = f"{cfg.category_api}?per_page=50&orderby=count&order=desc&_fields=id,name,slug,count"
        data = await http_client.get_json(url, ttl=settings.cache.categories_ttl)
        return parse_categories(data)

    async def get_category_items(self, cat_slug: str, page: int = 1) -> PaginatedResult:
        url = f"{cfg.base_url}/category/{cat_slug}/"
        if page > 1:
            url = f"{cfg.base_url}/category/{cat_slug}/page/{page}/"
        html = await http_client.get(url, ttl=settings.cache.listing_ttl)
        # Category pages can have both series and movies
        result = parse_listing_page(html, RE_SERIES_URL, "series")
        result.current_page = page
        return result


# Singleton
api = AnimeDekhoAPI()
