"""Managed aiohttp session with caching and cookie bypass."""

from __future__ import annotations
import hashlib
import logging

import aiohttp

from config.settings import settings
from .cache import TTLCache

log = logging.getLogger(__name__)


class HttpClient:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._cache = TTLCache(max_entries=settings.cache.max_entries)

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            jar = aiohttp.CookieJar()
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": settings.site.user_agent},
                timeout=aiohttp.ClientTimeout(total=settings.site.request_timeout),
                cookie_jar=jar,
            )
            # Set bypass cookie
            for name, val in settings.site.bypass_cookie.items():
                self._session.cookie_jar.update_cookies(
                    {name: val},
                    response_url=aiohttp.typedefs.URL(settings.site.base_url),
                )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        assert self._session and not self._session.closed, "Call start() first"
        return self._session

    @staticmethod
    def _key(url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    async def get(self, url: str, ttl: int | None = None) -> str:
        ttl = ttl if ttl is not None else settings.cache.default_ttl
        k = self._key(url)
        cached = await self._cache.get(k)
        if cached is not None:
            return cached
        await self.start()
        async with self._session.get(url) as r:
            r.raise_for_status()
            text = await r.text()
        await self._cache.set(k, text, ttl)
        return text

    async def post(self, url: str, data: dict, ttl: int | None = None) -> str:
        await self.start()
        if ttl:
            k = self._key(url + str(sorted(data.items())))
            cached = await self._cache.get(k)
            if cached is not None:
                return cached
        async with self._session.post(url, data=data) as r:
            r.raise_for_status()
            text = await r.text()
        if ttl:
            await self._cache.set(k, text, ttl)
        return text

    async def get_json(self, url: str, ttl: int | None = None):
        ttl = ttl if ttl is not None else settings.cache.default_ttl
        k = self._key(url + ":j")
        cached = await self._cache.get(k)
        if cached is not None:
            return cached
        await self.start()
        async with self._session.get(url) as r:
            r.raise_for_status()
            data = await r.json()
        await self._cache.set(k, data, ttl)
        return data

    # ── Uncached helpers (for shortener bypass & redirects) ───────

    async def get_text_no_cache(
        self, url: str, *, headers: dict | None = None
    ) -> str:
        """GET a URL without caching. Accepts extra headers."""
        await self.start()
        merged = dict(self._session.headers)
        if headers:
            merged.update(headers)
        async with self._session.get(url, headers=merged) as r:
            r.raise_for_status()
            return await r.text()

    async def post_no_cache(
        self, url: str, *, data: dict, headers: dict | None = None
    ) -> str:
        """POST form data without caching. Accepts extra headers."""
        await self.start()
        merged = dict(self._session.headers)
        if headers:
            merged.update(headers)
        async with self._session.post(url, data=data, headers=merged) as r:
            r.raise_for_status()
            return await r.text()

    async def get_with_redirects(
        self, url: str, *, headers: dict | None = None, max_redirects: int = 10
    ) -> tuple[str, str]:
        """
        Follow redirects manually and return ``(final_url, body_text)``.

        Useful when you need to know the final landing URL after a chain
        of 301/302/meta-refresh redirects.
        """
        await self.start()
        merged = dict(self._session.headers)
        if headers:
            merged.update(headers)

        current_url = url
        for _ in range(max_redirects):
            async with self._session.get(
                current_url,
                headers=merged,
                allow_redirects=False,
            ) as r:
                if r.status in (301, 302, 303, 307, 308):
                    location = r.headers.get("Location", "")
                    if not location:
                        break
                    # Handle relative redirects
                    if not location.startswith("http"):
                        from urllib.parse import urljoin
                        location = urljoin(current_url, location)
                    current_url = location
                    continue

                # Not a redirect — read the body
                text = await r.text()
                return (str(r.url), text)

        # Fell through max redirects — do a normal fetch of current URL
        async with self._session.get(current_url, headers=merged) as r:
            text = await r.text()
            return (str(r.url), text)

    async def post_follow_redirects(
        self, url: str, *, data: dict, headers: dict | None = None,
        max_redirects: int = 10,
    ) -> tuple[str, str]:
        """
        POST form data, follow redirects manually, return ``(body_text, final_url)``.
        """
        await self.start()
        merged = dict(self._session.headers)
        if headers:
            merged.update(headers)

        # First request is POST
        async with self._session.post(
            url, data=data, headers=merged, allow_redirects=False,
        ) as r:
            if r.status in (301, 302, 303, 307, 308):
                location = r.headers.get("Location", "")
                if location:
                    if not location.startswith("http"):
                        from urllib.parse import urljoin
                        location = urljoin(url, location)
                    # Follow remaining redirects with GET
                    final_url, text = await self.get_with_redirects(
                        location, headers=headers
                    )
                    return (text, final_url)
            text = await r.text()
            return (text, str(r.url))

    async def head_follow(self, url: str) -> str:
        """Follow redirects via HEAD and return the final URL (no body)."""
        await self.start()
        async with self._session.head(url, allow_redirects=True) as r:
            return str(r.url)


http_client = HttpClient()
