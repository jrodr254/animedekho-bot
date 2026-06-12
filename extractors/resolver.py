"""
Player URL resolver — attempts to extract direct video URLs
from known CDN player pages (Vimeo, StreamWish, FileMoon, etc.).

Also handles shortener bypass: if a URL is behind a link shortener,
it is resolved first before player extraction begins.
"""

from __future__ import annotations

import re
import logging

from utils.http import http_client
from extractors.shortener import detect_and_bypass, is_shortener

log = logging.getLogger(__name__)


# ── Public API ─────────────────────────────────────────────────────────


async def resolve_player_url(player_url: str) -> dict | None:
    """
    Given a CDN player embed URL (or shortener wrapping one), try to
    extract the direct stream URL.

    Returns: ``{"url": "...", "type": "m3u8|mp4", "quality": "..."}`` or ``None``.
    """
    if not player_url:
        return None

    # Step 1: bypass shortener if applicable
    resolved_url = await detect_and_bypass(player_url, http_client=http_client)

    domain = _get_domain(resolved_url)

    try:
        if "vimeo.com" in domain:
            return await _resolve_vimeo(resolved_url)
        elif any(x in domain for x in ("streamwish", "swish", "playerwish")):
            return await _resolve_packed_player(resolved_url)
        elif any(x in domain for x in ("filemoon", "kerapoxy")):
            return await _resolve_packed_player(resolved_url)
        elif "doodstream" in domain or "dood." in domain:
            return await _resolve_dood(resolved_url)
        elif any(x in domain for x in ("streamtape", "strtape")):
            return await _resolve_streamtape(resolved_url)
        elif "mp4upload" in domain:
            return await _resolve_packed_player(resolved_url)
        elif "vidguard" in domain or "vgfplay" in domain:
            return await _resolve_packed_player(resolved_url)
        else:
            # Generic: try packed first, then scan for m3u8/mp4
            return await _resolve_packed_player(resolved_url) or await _resolve_generic(resolved_url)
    except Exception as e:
        log.warning("Extractor failed for %s: %s", resolved_url, e)
        return None


# ── Helpers ────────────────────────────────────────────────────────────


def _get_domain(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.lower()


# ── eval(function(p,a,c,k,e,d)) unpacker ──────────────────────────────


def _unpack_packed_js(html: str) -> str | None:
    """
    Decode Dean Edwards / eval(function(p,a,c,k,e,d){...}) packed JS.

    The packer pattern:
        eval(function(p,a,c,k,e,d){...}('PAYLOAD',RADIX,COUNT,'DICT'.split('|'),...))

    We replicate the unpacking logic in pure Python.
    """
    # Find all packed blocks in the page
    packed_re = re.compile(
        r"""eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*[dr]\s*\)"""
        r"""\s*\{.*?\}\s*\(\s*'(.*?)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'(.*?)'\s*\.split\s*\(\s*'([|])'\s*\)""",
        re.DOTALL,
    )

    results: list[str] = []
    for m in packed_re.finditer(html):
        payload = m.group(1)
        radix = int(m.group(2))
        count = int(m.group(3))
        keywords = m.group(4).split(m.group(5))

        try:
            unpacked = _do_unpack(payload, radix, count, keywords)
            if unpacked:
                results.append(unpacked)
        except Exception as e:
            log.debug("Packed JS unpack error: %s", e)

    return "\n".join(results) if results else None


def _do_unpack(payload: str, radix: int, count: int, keywords: list[str]) -> str:
    """Core unpacker: substitute base-N tokens with dictionary words."""

    def _base_n(num: int, base: int) -> str:
        """Convert *num* to a base-*base* string (up to base 36)."""
        if num < 0:
            return "-" + _base_n(-num, base)
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        if num < base:
            return digits[num]
        return _base_n(num // base, base) + digits[num % base]

    # Build substitution lookup  (token → keyword)
    lookup: dict[str, str] = {}
    for i in range(count):
        token = _base_n(i, radix)
        if i < len(keywords) and keywords[i]:
            lookup[token] = keywords[i]
        else:
            lookup[token] = token

    # Replace word-boundary tokens in the payload
    def replacer(match: re.Match) -> str:
        word = match.group(0)
        return lookup.get(word, word)

    return re.sub(r'\b\w+\b', replacer, payload)


# ── Per-site resolvers ─────────────────────────────────────────────────


async def _resolve_vimeo(url: str) -> dict | None:
    """Vimeo player — extract from config JSON."""
    html = await http_client.get(url, ttl=60)
    m = re.search(r'"hls":\s*\{[^}]*"url":\s*"([^"]+)"', html)
    if m:
        return {"url": m.group(1), "type": "m3u8", "quality": "auto"}
    m = re.search(r'"url":\s*"(https://[^"]+\.mp4[^"]*)"', html)
    if m:
        return {"url": m.group(1), "type": "mp4", "quality": "auto"}
    return None


async def _resolve_packed_player(url: str) -> dict | None:
    """
    Generic packed/obfuscated player resolver.

    1. Fetch the page.
    2. Try to find m3u8/mp4 URLs in plain HTML.
    3. If not found, unpack any eval(function(p,a,c,k,e,d)...) blocks
       and search the unpacked JS for stream URLs.
    """
    html = await http_client.get(url, ttl=60)

    # Try plain-text patterns first
    result = _scan_for_stream(html)
    if result:
        return result

    # Unpack obfuscated JS and scan again
    unpacked = _unpack_packed_js(html)
    if unpacked:
        log.debug("Unpacked %d chars of packed JS from %s", len(unpacked), url[:60])
        result = _scan_for_stream(unpacked)
        if result:
            return result

    return None


def _scan_for_stream(text: str) -> dict | None:
    """Scan *text* for m3u8/mp4 stream URLs."""
    for pattern in [
        r'file\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r'source\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r'src\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r"file\s*:\s*'(https?://[^']+\.m3u8[^']*)'",
        r"source\s*:\s*'(https?://[^']+\.m3u8[^']*)'",
        r'sources\s*:\s*\[\s*\{[^}]*file\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
        r'file\s*:\s*"(https?://[^"]+\.mp4[^"]*)"',
        r'source\s*:\s*"(https?://[^"]+\.mp4[^"]*)"',
        r'"(https?://[^"]+master\.m3u8[^"]*)"',
        r'"(https?://[^"]+index\.m3u8[^"]*)"',
        r"'(https?://[^']+master\.m3u8[^']*)'",
        r"'(https?://[^']+index\.m3u8[^']*)'",
        r'"(https?://[^"]+\.m3u8[^"]*)"',
        r"'(https?://[^']+\.m3u8[^']*)'",
    ]:
        m = re.search(pattern, text)
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
    m = re.search(
        r"getElementById\('robotlink'\)\.innerHTML\s*=\s*'([^']+)'\s*\+\s*\('([^']+)'\)",
        html,
    )
    if m:
        direct = "https:" + m.group(1) + m.group(2)
        return {"url": direct, "type": "mp4", "quality": "auto"}
    return None


async def _resolve_generic(url: str) -> dict | None:
    """Last resort — scan page for any m3u8/mp4 URL."""
    html = await http_client.get(url, ttl=60)
    return _scan_for_stream(html)
