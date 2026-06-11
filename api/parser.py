"""HTML parsers for AnimeDekho pages."""

from __future__ import annotations
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from .models import (
    SearchResult, Series, Season, Episode, Movie,
    VideoServer, Category, PaginatedResult,
)
from utils.helpers import clean_title, slug_to_title


# ── Regex patterns ────────────────────────────────────────────────
RE_SERIES_URL = re.compile(
    r"https://animedekho\.app/series-hindi/([^/]+)/"
)
RE_MOVIE_URL = re.compile(
    r"https://animedekho\.app/movie-hindi/([^/]+)/"
)
RE_EPISODE_URL = re.compile(
    r"https://animedekho\.app/epi/([^/]+?)-(\d+)x(\d+)/"
)
RE_TRDEKHO = re.compile(
    r"trdekho(?:%3D|=)(\d+)(?:%26|&)trid(?:%3D|=)(\d+)(?:%26|&)trtype(?:%3D|=)(\d+)"
)
RE_PAGE = re.compile(r"/page/(\d+)/")


def parse_nonce(html: str) -> str | None:
    """Extract WP nonce from toronites JS config."""
    m = re.search(r'"nonce"\s*:\s*"([^"]+)"', html)
    return m.group(1) if m else None


def parse_search_html(html: str) -> list[SearchResult]:
    """Parse search AJAX response HTML."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()

    for article in soup.find_all("article"):
        link = article.find("a", href=True)
        if not link:
            continue
        href = link["href"]

        m_series = RE_SERIES_URL.match(href)
        m_movie = RE_MOVIE_URL.match(href)

        if m_series and m_series.group(1) not in seen:
            slug = m_series.group(1)
            seen.add(slug)
            title_tag = article.find(["h2", "h3", "h4"])
            title = title_tag.get_text(strip=True) if title_tag else slug_to_title(slug)
            poster = ""
            img = article.find("img")
            if img:
                poster = img.get("data-src") or img.get("src") or ""
            results.append(SearchResult(
                title=title, slug=slug, url=href,
                content_type="series", poster=poster,
            ))
        elif m_movie and m_movie.group(1) not in seen:
            slug = m_movie.group(1)
            seen.add(slug)
            title_tag = article.find(["h2", "h3", "h4"])
            title = title_tag.get_text(strip=True) if title_tag else slug_to_title(slug)
            poster = ""
            img = article.find("img")
            if img:
                poster = img.get("data-src") or img.get("src") or ""
            results.append(SearchResult(
                title=title, slug=slug, url=href,
                content_type="movie", poster=poster,
            ))

    return results


def parse_listing_page(html: str, url_re: re.Pattern, content_type: str) -> PaginatedResult:
    """Parse a series or movies listing page."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()

    for a in soup.find_all("a", href=True):
        m = url_re.match(a["href"])
        if not m or m.group(1) in seen:
            continue
        slug = m.group(1)
        seen.add(slug)

        title = a.get_text(strip=True)
        if not title or title.lower() in ("watch series", "series", "watch movies", "movies"):
            title = slug_to_title(slug)

        poster = ""
        img = a.find("img")
        if img:
            poster = img.get("data-src") or img.get("src") or ""

        items.append(SearchResult(
            title=title, slug=slug, url=a["href"],
            content_type=content_type, poster=poster,
        ))

    pages = [int(x) for x in RE_PAGE.findall(html)]
    max_page = max(pages) if pages else 1

    return PaginatedResult(items=items, current_page=1, max_page=max_page)


def parse_series_detail(html: str, slug: str) -> Series:
    """Parse a series page — extract seasons, episodes, metadata."""
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title_tag = soup.find("title")
    title = clean_title(title_tag.get_text(strip=True)) if title_tag else slug_to_title(slug)

    # Poster
    poster = None
    og = soup.find("meta", property="og:image")
    if og:
        poster = og.get("content")

    # Description
    desc = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta:
        desc = meta.get("content", "")

    # Genres
    genres = []
    for a in soup.find_all("a", href=re.compile(r"/category/")):
        g = a.get_text(strip=True)
        if g and g not in genres:
            genres.append(g)

    # Episodes grouped by season
    ep_matches = RE_EPISODE_URL.findall(html)
    seasons: dict[int, Season] = {}
    for ep_slug_full, s_num, e_num in ep_matches:
        s = int(s_num)
        e = int(e_num)
        full_slug = f"{ep_slug_full}-{s_num}x{e_num}"
        ep = Episode(number=e, slug=full_slug, season=s)
        seasons.setdefault(s, Season(number=s))
        # Avoid duplicates
        if not any(x.number == e for x in seasons[s].episodes):
            seasons[s].episodes.append(ep)

    for s in seasons.values():
        s.episodes.sort(key=lambda x: x.number)

    return Series(
        title=title, slug=slug,
        url=f"https://animedekho.app/series-hindi/{slug}/",
        description=desc[:800], poster=poster,
        genres=genres[:10], seasons=seasons,
    )


def parse_episode_page(html: str, ep_slug: str) -> Episode:
    """Parse episode page — extract post ID and default server URL."""
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("title")
    title = clean_title(title_tag.get_text(strip=True)) if title_tag else ep_slug

    # Extract season x episode from slug
    m = re.match(r".*-(\d+)x(\d+)$", ep_slug)
    season = int(m.group(1)) if m else 0
    ep_num = int(m.group(2)) if m else 0

    # Extract post_id from trdekho URL in inline script
    post_id = 0
    trdekho_match = RE_TRDEKHO.search(html)
    if trdekho_match:
        post_id = int(trdekho_match.group(2))

    # Extract servers from bx-lst (base64 data-src)
    import base64
    servers = []
    ul = soup.find("ul", class_="bx-lst")
    if ul:
        for a in ul.find_all("a"):
            name = a.get_text(strip=True)
            data_src = a.get("data-src", "")
            if data_src:
                try:
                    decoded = base64.b64decode(data_src).decode("utf-8")
                    srv_match = RE_TRDEKHO.search(decoded)
                    srv_id = int(srv_match.group(1)) if srv_match else -1
                    servers.append(VideoServer(
                        name=name or f"Server {srv_id}",
                        server_id=srv_id,
                        proxy_url=decoded,
                    ))
                except Exception:
                    pass

    # If no servers from bx-lst, build from known trdekho IDs
    if not servers and post_id:
        from config.settings import settings
        for sid, sname in settings.site.server_ids.items():
            proxy = f"https://animedekho.app/?trdekho={sid}&trid={post_id}&trtype=2"
            servers.append(VideoServer(name=sname, server_id=sid, proxy_url=proxy))

    return Episode(
        number=ep_num, slug=ep_slug, season=season,
        title=title, post_id=post_id, servers=servers,
        page_url=f"https://animedekho.app/epi/{ep_slug}/",
    )


def parse_movie_page(html: str, slug: str) -> Movie:
    """Parse movie page — similar to episode but for movies."""
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("title")
    title = clean_title(title_tag.get_text(strip=True)) if title_tag else slug_to_title(slug)

    poster = None
    og = soup.find("meta", property="og:image")
    if og:
        poster = og.get("content")

    desc = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta:
        desc = meta.get("content", "")

    genres = []
    for a in soup.find_all("a", href=re.compile(r"/category/")):
        g = a.get_text(strip=True)
        if g and g not in genres:
            genres.append(g)

    # Extract post_id
    post_id = 0
    trdekho_match = RE_TRDEKHO.search(html)
    if trdekho_match:
        post_id = int(trdekho_match.group(2))

    # Extract servers
    import base64
    servers = []
    ul = soup.find("ul", class_="bx-lst")
    if ul:
        for a in ul.find_all("a"):
            name = a.get_text(strip=True)
            data_src = a.get("data-src", "")
            if data_src:
                try:
                    decoded = base64.b64decode(data_src).decode("utf-8")
                    srv_match = RE_TRDEKHO.search(decoded)
                    srv_id = int(srv_match.group(1)) if srv_match else -1
                    servers.append(VideoServer(
                        name=name, server_id=srv_id, proxy_url=decoded,
                    ))
                except Exception:
                    pass

    if not servers and post_id:
        from config.settings import settings
        for sid, sname in settings.site.server_ids.items():
            proxy = f"https://animedekho.app/?trdekho={sid}&trid={post_id}&trtype=2"
            servers.append(VideoServer(name=sname, server_id=sid, proxy_url=proxy))

    return Movie(
        title=title, slug=slug,
        url=f"https://animedekho.app/movie-hindi/{slug}/",
        description=desc[:800], poster=poster,
        genres=genres[:10], post_id=post_id, servers=servers,
    )


def parse_categories(data: list[dict]) -> list[Category]:
    """Parse WP REST API categories response."""
    cats = []
    for c in data:
        if c.get("count", 0) > 5:
            cats.append(Category(
                id=c["id"], name=c["name"], slug=c["slug"], count=c["count"],
            ))
    cats.sort(key=lambda x: x.count, reverse=True)
    return cats[:20]
