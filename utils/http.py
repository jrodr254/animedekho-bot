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


http_client = HttpClient()
