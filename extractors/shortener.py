"""
Link shortener bypass module.

Bypasses these shortener families:
  - gplinks.co (+ gplinks.in, gplink.in)
  - vshort.in (+ vshort.me) 
  - cuty.io (+ cutt.ly variants)

All three use similar patterns:
  1. GET the short URL → page with countdown/ad
  2. Extract a hidden token/form or encoded destination
  3. POST or follow redirect to get the real URL
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from urllib.parse import urlparse, urljoin, parse_qs, unquote

log = logging.getLogger(__name__)

# ── Domain detection ───────────────────────────────────────────────────

_SHORTENER_DOMAINS: dict[str, str] = {
    # domain fragment → handler key
    "gplinks.co": "gplinks",
    "gplinks.in": "gplinks",
    "gplink.in": "gplinks",
    "gplinks.com": "gplinks",
    "vshort.in": "vshort",
    "vshort.me": "vshort",
    "vshortener.com": "vshort",
    "cuty.io": "cuty",
    "cutt.ly": "cuty",
    "cuty.me": "cuty",
}


def is_shortener(url: str) -> bool:
    """Return True if the URL belongs to a known shortener domain."""
    try:
        host = urlparse(url).netloc.lower()
        return any(domain in host for domain in _SHORTENER_DOMAINS)
    except Exception:
        return False


def _identify(url: str) -> str | None:
    """Return handler key for the URL, or None."""
    try:
        host = urlparse(url).netloc.lower()
        for domain, key in _SHORTENER_DOMAINS.items():
            if domain in host:
                return key
    except Exception:
        pass
    return None


# ── Main entry points ──────────────────────────────────────────────────


async def bypass_shortener(url: str, *, http_client=None) -> str | None:
    """
    Bypass a link shortener and return the final destination URL.
    Returns None if bypass failed.
    """
    if http_client is None:
        from utils.http import http_client as _hc
        http_client = _hc

    handler = _identify(url)
    log.info("Shortener bypass [%s] for %s", handler, url)

    try:
        if handler == "gplinks":
            return await _bypass_gplinks(url, http_client)
        elif handler == "vshort":
            return await _bypass_vshort(url, http_client)
        elif handler == "cuty":
            return await _bypass_cuty(url, http_client)
        else:
            return await _bypass_generic(url, http_client)
    except Exception as e:
        log.warning("Shortener bypass failed for %s: %s", url, e)
        return None


async def detect_and_bypass(url: str, *, http_client=None) -> str:
    """
    If *url* is a known shortener, bypass it and return the destination.
    Otherwise return *url* unchanged. Safe to call on any URL.
    """
    if is_shortener(url):
        resolved = await bypass_shortener(url, http_client=http_client)
        if resolved:
            log.info("Bypassed: %s → %s", url[:60], resolved[:60])
            return resolved
        log.warning("Bypass returned None for %s, returning original", url[:60])
    return url


# ── GPLinks bypass ─────────────────────────────────────────────────────


async def _bypass_gplinks(url: str, http_client) -> str | None:
    """
    GPLinks.co bypass flow:
    
    1. GET the short URL → HTML page with encoded data
    2. Page contains a JS variable or hidden form with base64-encoded URL
       OR a /go endpoint that accepts a POST with token
    3. Sometimes uses intermediate page with countdown timer
    
    GPLinks typically:
    - Sets cookies on first visit
    - Has a "go" form with _token field  
    - May use atob() for the destination
    - Sometimes embeds URL in a script as encoded string
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # Step 1: GET the shortener page
    html = await http_client.get_text_no_cache(url, headers={
        "Referer": base + "/",
        "Accept": "text/html,application/xhtml+xml",
    })

    # Strategy 1: Look for the go form with _token
    # GPLinks uses a form that POSTs to the same URL with a _token
    dest = await _gplinks_form_bypass(html, url, base, http_client)
    if dest:
        return dest

    # Strategy 2: atob() / base64 encoded destination in JS
    dest = _extract_atob(html)
    if dest:
        return dest

    # Strategy 3: Look for encoded URL in script tags
    # GPLinks sometimes stores the URL in a var like: var url = "base64string"
    dest = _extract_encoded_var(html)
    if dest:
        return dest

    # Strategy 4: meta refresh
    dest = _extract_meta_refresh(html)
    if dest:
        return dest

    # Strategy 5: window.location redirect
    dest = _extract_js_redirect(html)
    if dest:
        return dest

    return None


async def _gplinks_form_bypass(html: str, page_url: str, base: str, http_client) -> str | None:
    """
    GPLinks form-based bypass:
    Find the form with _token, extract all hidden inputs,
    POST to the form action, then extract redirect from response.
    """
    # Find CSRF token
    token_match = re.search(
        r'name=["\']_token["\']\s*value=["\']([^"\']+)["\']', html
    ) or re.search(
        r'value=["\']([^"\']+)["\']\s*name=["\']_token["\']', html
    )
    if not token_match:
        # Also try meta tag csrf
        token_match = re.search(
            r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html
        )
    
    if not token_match:
        return None

    token = token_match.group(1)

    # Find all hidden inputs
    data = {"_token": token}
    for m in re.finditer(
        r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
        html, re.IGNORECASE
    ):
        data[m.group(1)] = m.group(2)
    for m in re.finditer(
        r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\'][^>]*type=["\']hidden["\']',
        html, re.IGNORECASE
    ):
        data[m.group(1)] = m.group(2)

    # Find form action
    form_match = re.search(r'<form[^>]*action=["\']([^"\']*)["\']', html, re.IGNORECASE)
    action = form_match.group(1) if form_match else page_url
    if not action.startswith("http"):
        action = urljoin(page_url, action)

    log.debug("GPLinks form POST to %s with %d fields", action, len(data))

    try:
        resp_html, final_url = await http_client.post_follow_redirects(
            action, data=data, headers={
                "Referer": page_url,
                "Origin": base,
            }
        )

        # If we got redirected to a non-shortener URL, that's our destination
        if final_url and not is_shortener(final_url):
            return final_url

        # Otherwise parse the response for the destination
        for extractor in (_extract_meta_refresh, _extract_atob, _extract_js_redirect, _extract_encoded_var):
            dest = extractor(resp_html)
            if dest:
                return dest

    except Exception as e:
        log.warning("GPLinks form POST failed: %s", e)

    return None


# ── VShort bypass ──────────────────────────────────────────────────────


async def _bypass_vshort(url: str, http_client) -> str | None:
    """
    VShort.in bypass flow:
    
    1. GET the short URL → countdown page
    2. Page has either:
       a) A hidden form that submits after countdown
       b) An AJAX call to an API endpoint that returns the destination  
       c) Base64-encoded URL in inline script
    3. May need to wait/simulate the countdown via a second request
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    html = await http_client.get_text_no_cache(url, headers={
        "Referer": base + "/",
    })

    # Strategy 1: Look for API/AJAX endpoint that returns the link
    # VShort often has: $.ajax({ url: '/links/go', data: {id: X, token: Y} })
    dest = await _vshort_ajax_bypass(html, url, base, http_client)
    if dest:
        return dest

    # Strategy 2: Hidden form with token (similar to GPLinks)
    dest = await _vshort_form_bypass(html, url, base, http_client)
    if dest:
        return dest

    # Strategy 3: atob / base64
    dest = _extract_atob(html)
    if dest:
        return dest

    # Strategy 4: Encoded URL in a JS var or data attribute
    dest = _extract_encoded_var(html)
    if dest:
        return dest

    # Strategy 5: meta refresh / JS redirect
    dest = _extract_meta_refresh(html)
    if dest:
        return dest
    dest = _extract_js_redirect(html)
    if dest:
        return dest

    return None


async def _vshort_ajax_bypass(html: str, page_url: str, base: str, http_client) -> str | None:
    """
    VShort AJAX bypass: find the go/redirect API call, extract params, call it directly.
    """
    # Look for AJAX URL patterns
    # Pattern: url: '/links/go' or '/api/links/go' etc.
    ajax_url_match = re.search(
        r"""(?:url|action)\s*:\s*['"](/[^'"]*(?:links|go|redirect)[^'"]*)['"]\s*""",
        html
    )
    
    # Look for the link ID / alias
    id_match = re.search(r'(?:id|alias|link_id)\s*:\s*["\']?(\w+)["\']?', html)
    token_match = re.search(
        r'name=["\']_token["\']\s*value=["\']([^"\']+)["\']', html
    ) or re.search(
        r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html
    )

    if ajax_url_match and (id_match or token_match):
        ajax_path = ajax_url_match.group(1)
        ajax_url = base + ajax_path

        data = {}
        if id_match:
            data["id"] = id_match.group(1)
        if token_match:
            data["_token"] = token_match.group(1)

        try:
            resp = await http_client.post_no_cache(ajax_url, data=data, headers={
                "Referer": page_url,
                "X-Requested-With": "XMLHttpRequest",
            })
            
            # Response might be JSON with url field
            try:
                j = json.loads(resp)
                if isinstance(j, dict):
                    dest = j.get("url") or j.get("redirect") or j.get("link") or j.get("destination")
                    if dest and dest.startswith("http"):
                        return dest
            except (json.JSONDecodeError, ValueError):
                pass

            # Or it might be the URL directly
            if resp.strip().startswith("http"):
                return resp.strip()

            # Or HTML with redirect
            for extractor in (_extract_meta_refresh, _extract_js_redirect, _extract_atob):
                dest = extractor(resp)
                if dest:
                    return dest

        except Exception as e:
            log.debug("VShort AJAX bypass failed: %s", e)

    return None


async def _vshort_form_bypass(html: str, page_url: str, base: str, http_client) -> str | None:
    """VShort form bypass — similar to GPLinks form approach."""
    # Find all forms
    forms = re.finditer(
        r'<form[^>]*action=["\']([^"\']*)["\'][^>]*>(.*?)</form>',
        html, re.DOTALL | re.IGNORECASE
    )

    for form_match in forms:
        action = form_match.group(1)
        form_body = form_match.group(2)

        if not action.startswith("http"):
            action = urljoin(page_url, action)

        # Skip if action is to external ad/tracking
        action_host = urlparse(action).netloc.lower()
        page_host = urlparse(page_url).netloc.lower()
        if action_host and action_host != page_host:
            continue

        # Extract hidden inputs
        data = {}
        for inp in re.finditer(
            r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
            form_body, re.IGNORECASE
        ):
            data[inp.group(1)] = inp.group(2)
        for inp in re.finditer(
            r'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']([^"\']+)["\']',
            form_body, re.IGNORECASE
        ):
            data[inp.group(2)] = inp.group(1)

        if not data:
            continue

        try:
            resp_html, final_url = await http_client.post_follow_redirects(
                action, data=data, headers={
                    "Referer": page_url,
                    "Origin": base,
                }
            )

            if final_url and not is_shortener(final_url):
                return final_url

            for extractor in (_extract_meta_refresh, _extract_atob, _extract_js_redirect):
                dest = extractor(resp_html)
                if dest:
                    return dest
        except Exception as e:
            log.debug("VShort form POST failed: %s", e)

    return None


# ── Cuty.io bypass ─────────────────────────────────────────────────────


async def _bypass_cuty(url: str, http_client) -> str | None:
    """
    Cuty.io bypass flow:
    
    1. GET the short URL → page with obfuscated JS
    2. Cuty typically uses:
       a) A multi-step redirect with cookies
       b) Encoded destination in inline script (often double-base64 or reversed)
       c) An API call after countdown
    3. The real URL is often in a data-url attribute or encoded variable
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    html = await http_client.get_text_no_cache(url, headers={
        "Referer": base + "/",
    })

    # Strategy 1: Look for data-url or data-href attributes
    dest = _extract_data_attributes(html)
    if dest:
        return dest

    # Strategy 2: Cuty often has the URL in a script with specific patterns
    dest = _extract_cuty_script(html)
    if dest:
        return dest

    # Strategy 3: atob / base64
    dest = _extract_atob(html)
    if dest:
        return dest

    # Strategy 4: Form-based (similar to others)
    dest = await _cuty_form_bypass(html, url, base, http_client)
    if dest:
        return dest

    # Strategy 5: Try AJAX endpoint
    dest = await _cuty_ajax_bypass(html, url, base, http_client)
    if dest:
        return dest

    # Strategy 6: meta refresh / JS redirect
    dest = _extract_meta_refresh(html)
    if dest:
        return dest
    dest = _extract_js_redirect(html)
    if dest:
        return dest

    return None


def _extract_cuty_script(html: str) -> str | None:
    """
    Cuty.io specific: look for encoded URL patterns in their scripts.
    They often use patterns like:
    - var href = atob(atob("doublebase64"))
    - String.fromCharCode() arrays
    - Reversed strings
    """
    # Double base64 pattern: atob(atob("..."))
    for m in re.finditer(r'atob\s*\(\s*atob\s*\(\s*["\']([A-Za-z0-9+/=]+)["\']\s*\)\s*\)', html):
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="ignore")
            decoded2 = base64.b64decode(decoded).decode("utf-8", errors="ignore")
            if decoded2.startswith("http"):
                return decoded2
        except Exception:
            continue

    # String.fromCharCode pattern
    for m in re.finditer(r'String\.fromCharCode\s*\(([\d,\s]+)\)', html):
        try:
            chars = [int(x.strip()) for x in m.group(1).split(",") if x.strip()]
            decoded = "".join(chr(c) for c in chars)
            if decoded.startswith("http"):
                return decoded
        except Exception:
            continue

    # Reversed string pattern: "...".split("").reverse().join("")
    for m in re.finditer(r'["\']([^"\']{20,})["\']\.split\s*\(\s*["\']["\'].*?\.reverse\s*\(\s*\)\.join', html):
        try:
            reversed_str = m.group(1)[::-1]
            if reversed_str.startswith("http"):
                return reversed_str
        except Exception:
            continue

    return None


def _extract_data_attributes(html: str) -> str | None:
    """Extract destination from data-url, data-href, data-link attributes."""
    for attr in ("data-url", "data-href", "data-link", "data-redirect"):
        m = re.search(rf'{attr}\s*=\s*["\']([^"\']+)["\']', html)
        if m:
            val = m.group(1)
            # Might be base64 encoded
            if not val.startswith("http"):
                try:
                    decoded = base64.b64decode(val).decode("utf-8", errors="ignore")
                    if decoded.startswith("http"):
                        return decoded
                except Exception:
                    pass
            else:
                if not is_shortener(val):
                    return val
    return None


async def _cuty_form_bypass(html: str, page_url: str, base: str, http_client) -> str | None:
    """Cuty form bypass — extract and submit any redirect forms."""
    forms = re.finditer(
        r'<form[^>]*action=["\']([^"\']*)["\'][^>]*method=["\']post["\'][^>]*>(.*?)</form>',
        html, re.DOTALL | re.IGNORECASE
    )

    for form_match in forms:
        action = form_match.group(1)
        form_body = form_match.group(2)

        if not action.startswith("http"):
            action = urljoin(page_url, action)

        data = {}
        for inp in re.finditer(
            r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
            form_body, re.IGNORECASE
        ):
            data[inp.group(1)] = inp.group(2)
        for inp in re.finditer(
            r'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']([^"\']+)["\']',
            form_body, re.IGNORECASE
        ):
            data[inp.group(2)] = inp.group(1)

        if not data:
            continue

        try:
            resp_html, final_url = await http_client.post_follow_redirects(
                action, data=data, headers={
                    "Referer": page_url,
                    "Origin": base,
                }
            )
            if final_url and not is_shortener(final_url):
                return final_url

            for extractor in (_extract_meta_refresh, _extract_atob, _extract_js_redirect):
                dest = extractor(resp_html)
                if dest:
                    return dest
        except Exception as e:
            log.debug("Cuty form POST failed: %s", e)

    return None


async def _cuty_ajax_bypass(html: str, page_url: str, base: str, http_client) -> str | None:
    """Cuty AJAX bypass — look for API endpoints in the scripts."""
    # Look for fetch/ajax calls
    api_match = re.search(
        r"""(?:fetch|ajax|post)\s*\(\s*['"](/[^'"]*(?:go|redirect|link|click)[^'"]*)['"]\s*""",
        html
    )
    if not api_match:
        return None

    api_path = api_match.group(1)
    api_url = base + api_path

    # Extract any token/id
    token_match = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html)
    id_match = re.search(r'["\'](?:id|alias|code)["\']:\s*["\'](\w+)["\']', html)

    data = {}
    if token_match:
        data["_token"] = token_match.group(1)
    if id_match:
        data["id"] = id_match.group(1)

    if not data:
        return None

    try:
        resp = await http_client.post_no_cache(api_url, data=data, headers={
            "Referer": page_url,
            "X-Requested-With": "XMLHttpRequest",
        })

        try:
            j = json.loads(resp)
            if isinstance(j, dict):
                dest = j.get("url") or j.get("redirect") or j.get("link") or j.get("destination")
                if dest and dest.startswith("http"):
                    return dest
        except (json.JSONDecodeError, ValueError):
            pass

        if resp.strip().startswith("http"):
            return resp.strip()

    except Exception as e:
        log.debug("Cuty AJAX bypass failed: %s", e)

    return None


# ── Generic bypass (fallback) ──────────────────────────────────────────


async def _bypass_generic(url: str, http_client) -> str | None:
    """Generic bypass: tries all known extraction strategies."""
    html = await http_client.get_text_no_cache(url)

    for extractor in (_extract_meta_refresh, _extract_atob, _extract_encoded_var,
                      _extract_data_attributes, _extract_js_redirect):
        dest = extractor(html)
        if dest:
            return dest

    return None


# ── Shared extraction strategies ───────────────────────────────────────


def _extract_meta_refresh(html: str) -> str | None:
    """Extract URL from <meta http-equiv="refresh" ...> tag."""
    m = re.search(
        r'<meta[^>]*http-equiv\s*=\s*["\']refresh["\'][^>]*content\s*=\s*["\'][^"\']*url\s*=\s*([^"\'>\s]+)',
        html, re.IGNORECASE,
    )
    if m:
        dest = m.group(1).strip()
        if dest.startswith("http"):
            return dest
    return None


def _extract_atob(html: str) -> str | None:
    """Decode atob('BASE64') calls and raw base64 strings that decode to URLs."""
    # Explicit atob('...')
    for m in re.finditer(r'atob\s*\(\s*["\']([A-Za-z0-9+/=]+)["\']\s*\)', html):
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="ignore")
            if decoded.startswith("http"):
                return decoded
        except Exception:
            continue

    # Long base64 strings that might be URLs
    for m in re.finditer(r'["\']([A-Za-z0-9+/=]{40,})["\']', html):
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="ignore")
            if decoded.startswith("http"):
                return decoded
        except Exception:
            continue

    return None


def _extract_js_redirect(html: str) -> str | None:
    """Extract URL from window.location / location.href assignments."""
    patterns = [
        r'(?:window|document)\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']',
        r'location\.replace\s*\(\s*["\']([^"\']+)["\']\s*\)',
        r'location\.assign\s*\(\s*["\']([^"\']+)["\']\s*\)',
        r'window\.open\s*\(\s*["\']([^"\']+)["\']\s*[,)]',
    ]
    for pat in patterns:
        for m in re.finditer(pat, html, re.IGNORECASE):
            url = m.group(1)
            if url.startswith("http"):
                return url
    return None


def _extract_encoded_var(html: str) -> str | None:
    """
    Look for JS variables containing encoded/escaped URLs.
    Patterns like: var link = "aHR0cHM6Ly..." or var url = decodeURIComponent("...")
    """
    # decodeURIComponent pattern
    for m in re.finditer(r'decodeURIComponent\s*\(\s*["\']([^"\']+)["\']\s*\)', html):
        try:
            decoded = unquote(m.group(1))
            if decoded.startswith("http"):
                return decoded
        except Exception:
            continue

    # Hex-escaped string: "\x68\x74\x74\x70..."
    for m in re.finditer(r'["\']((\\x[0-9a-fA-F]{2}){10,})["\']', html):
        try:
            decoded = m.group(1).encode().decode("unicode_escape")
            if decoded.startswith("http"):
                return decoded
        except Exception:
            continue

    return None
