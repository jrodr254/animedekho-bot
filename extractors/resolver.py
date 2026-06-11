"""
Player URL resolver — attempts to extract direct video URLs
from known CDN player pages (Vimeo, StreamWish, FileMoon, etc.).
"""

from __future__ import annotations
import re
import logging

from utils.http import http_client

log = logging.getLogger(__name__)


async def resolve_player_url(player_url: str) -> dict | None:
    """
    Given a CDN player embed URL, try to extract the direct stream URL.

    Returns: {"url": "...", "type": "m3u8|mp4", "quality": "..."} or None.
    """
    if not player_url:
        return None

    domain = _get_domain(player_url)

    try:
        if "vimeo.com" in domain:
            return await _resolve_vimeo(player_url)
        elif any(x in domain for x in ("streamwish", "swish", "playerwish")):
            return await _resolve_packed_player(player_url)
        elif any(x in domain for x in ("filemoon", "kerapoxy")):
            return await _resolve_packed_player(player_url)
        elif "doodstream" in domain or "dood." in domain:
            return await _resolve_dood(player_url)
        elif any(x in domain for x in ("streamtape", "strtape")):
            return await _resolve_streamtape(player_url)
        elif "mp4upload" in domain:
            return await _resolve_packed_player(player_url)
        elif "vidguard" in domain or "vgfplay" in domain:
            return await _resolve_packed_player(player_url)
        else:
            # Generic: try to find m3u8/mp4 in page source
            return await _resolve_generic(player_url)
    except Exception as e:
        log.warning("Extractor failed for %s: %s", player_url, e)
        return None


def _get_domain(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.lower()


async def _resolve_vimeo(url: str) -> dict | None:
    """Vimeo player — extract from config JSON."""
    html = await http_client.get(url, ttl=60)
    # Look for progressive or HLS sources
    m = re.search(r'"hls":\s*\{[^}]*"url":\s*"([^"]+)"', html)
    if m:
        return {"url": m.group(1), "type": "m3u8", "quality": "auto"}
    m = re.search(r'"url":\s*"(https://[^"]+\.mp4[^"]*)"', html)
    if m:
        return {"url": m.group(1), "type": "mp4", "quality": "auto"}
    return None


async def _resolve_packed_player(url: str) -> dict | None:
    """Generic packed/obfuscated player — look for file/source URL."""
    html = await http_client.get(url, ttl=60)
    # Try common patterns
    for pattern in [
        r'file\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r'source\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r'src\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r'file\s*:\s*"(https?://[^"]+\.mp4[^"]*)"',
        r'source\s*:\s*"(https?://[^"]+\.mp4[^"]*)"',
        r'"(https?://[^"]+master\.m3u8[^"]*)"',
        r'"(https?://[^"]+index\.m3u8[^"]*)"',
    ]:
        m = re.search(pattern, html)
        if m:
            video_url = m.group(1)
            vtype = "m3u8" if ".m3u8" in video_url else "mp4"
            return {"url": video_url, "type": vtype, "quality": "auto"}
    return None


async def _resolve_dood(url: str) -> dict | None:
    """Doodstream — these change rapidly, return embed URL."""
    # Dood uses dynamic token generation, hard to extract without JS
    return None


async def _resolve_streamtape(url: str) -> dict | None:
    """Streamtape extractor."""
    html = await http_client.get(url, ttl=60)
    m = re.search(r"getElementById\('robotlink'\)\.innerHTML\s*=\s*'([^']+)'\s*\+\s*\('([^']+)'\)", html)
    if m:
        direct = "https:" + m.group(1) + m.group(2)
        return {"url": direct, "type": "mp4", "quality": "auto"}
    return None


async def _resolve_generic(url: str) -> dict | None:
    """Last resort — scan page for any m3u8/mp4 URL."""
    html = await http_client.get(url, ttl=60)
    m = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
    if m:
        return {"url": m.group(1), "type": "m3u8", "quality": "auto"}
    m = re.search(r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)', html)
    if m:
        return {"url": m.group(1), "type": "mp4", "quality": "auto"}
    return None
