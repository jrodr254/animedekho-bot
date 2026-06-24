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

        # The site requires visiting a verification shortlink before
        # server data is shown. Extract and visit it, then re-fetch.
        shortlink_match = re.search(
            r'name=["\']shortlink["\'][^>]*value=["\']([^"\']+)["\']',
            html,
        )
        if shortlink_match:
            shortlink = shortlink_match.group(1)
            log.debug("Visiting verification shortlink: %s", shortlink)
            try:
                # Visit the shortlink — this sets the verification cookie
                await http_client.get_no_cache(shortlink)
                # Re-fetch the episode page — servers are now visible
                html = await http_client.get_no_cache(url)
            except Exception as e:
                log.warning("Verification shortlink failed: %s", e)

        return parse_episode_page(html, ep_slug)

    async def get_movie(self, slug: str) -> Movie:
        url = f"{cfg.base_url}/movie-hindi/{slug}/"
        html = await http_client.get(url)

        # Same verification shortlink flow as episodes
        shortlink_match = re.search(
            r'name=["\']shortlink["\'][^>]*value=["\']([^"\']+)["\']',
            html,
        )
        if shortlink_match:
            shortlink = shortlink_match.group(1)
            log.debug("Visiting verification shortlink: %s", shortlink)
            try:
                await http_client.get_no_cache(shortlink)
                html = await http_client.get_no_cache(url)
            except Exception as e:
                log.warning("Verification shortlink failed: %s", e)

        return parse_movie_page(html, slug)

    # ── Video server resolution ───────────────────────────────────

    async def resolve_server(self, server: VideoServer) -> VideoServer:
        """
        Fetch proxy URL, bypass shorteners if needed, and extract the
        real player/stream URL.

        Flow:
          1. GET the proxy_url (AnimeDekho's ``?trdekho=`` redirect page).
          2. Look for an ``<iframe src="...">`` pointing to the player.
          3. If the iframe src (or the page itself) goes through a link
             shortener, bypass it to get the real player embed.
          4. Check for direct m3u8/mp4 links in the page source.
        """
        from extractors.shortener import detect_and_bypass, is_shortener

        if not server.proxy_url:
            return server
        try:
            # Step 1: Fetch the proxy page (may be a redirect itself)
            final_url, html = await http_client.get_with_redirects(
                server.proxy_url,
                headers={"Referer": cfg.base_url + "/"},
            )

            # Step 2: If the whole page landed on a shortener, bypass it
            if is_shortener(final_url):
                bypassed = await detect_and_bypass(final_url)
                if bypassed and bypassed != final_url:
                    final_url, html = await http_client.get_with_redirects(bypassed)

            # Step 3: Look for <iframe src="..."> in the page
            iframe_match = re.search(r'<iframe[^>]*src\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
            if iframe_match:
                iframe_src = iframe_match.group(1)
                # The iframe URL itself might be a shortener
                iframe_src = await detect_and_bypass(iframe_src)
                server.player_url = iframe_src
            else:
                # Step 4: Check for shortener links in the page body
                # (some pages don't use iframes but have direct links)
                link_match = re.search(
                    r'href\s*=\s*["\']?(https?://[^\s"\'<>]+)["\']?',
                    html,
                )
                if link_match and is_shortener(link_match.group(1)):
                    bypassed = await detect_and_bypass(link_match.group(1))
                    if bypassed:
                        server.player_url = bypassed

            # Step 5: Also scan for direct video URLs in case the page
            # already contains the stream without needing a player
            if not server.player_url:
                m2 = re.search(
                    r'(?:src|url|file)\s*[=:]\s*["\']?(https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*)',
                    html,
                )
                if m2:
                    server.direct_url = m2.group(1)
                    server.video_type = "m3u8" if ".m3u8" in m2.group(1) else "mp4"

        except Exception as e:
            log.warning("Failed to resolve server %s: %s", server.name, e)
        return server

    async def resolve_all_servers(self, servers: list[VideoServer]) -> list[VideoServer]:
        """Resolve all servers, return only available ones.
        
        Servers are sorted by priority (VidStream → HydraX → Vidmoly)
        so the preferred server appears first in the result list.
        """
        import asyncio
        from config.settings import settings
        preferred = settings.site.preferred_servers  # ["VidStream", "HydraX", "Vidmoly"]

        def _server_priority(srv):
            name_lower = srv.name.lower()
            for i, pref in enumerate(preferred):
                if pref.lower() in name_lower:
                    return i
            return len(preferred)

        # Sort by priority before resolving
        sorted_servers = sorted(servers, key=_server_priority)

        tasks = [self.resolve_server(s) for s in sorted_servers]
        resolved = await asyncio.gather(*tasks, return_exceptions=True)
        available = [s for s in resolved if isinstance(s, VideoServer) and s.is_available]

        # Re-sort available servers by priority (gather preserves order, but be safe)
        available.sort(key=_server_priority)
        return available

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
