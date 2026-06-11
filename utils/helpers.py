"""Shared utility functions."""

from __future__ import annotations
import html as htmlmod
import re

from config.settings import settings


def esc(text: str) -> str:
    return htmlmod.escape(text)


def truncate(text: str, maxlen: int = 400) -> str:
    return text if len(text) <= maxlen else text[:maxlen - 1] + "…"


def short_slug(slug: str, maxlen: int | None = None) -> str:
    return slug[: maxlen or settings.bot.slug_max_len]


def clean_title(raw: str) -> str:
    for sep in ("–", "|", " - AnimeDekho", " - Watch"):
        raw = raw.split(sep)[0]
    return raw.strip()


def slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").title()


def extract_series_slug(ep_slug: str) -> str | None:
    m = re.match(r"(.+)-\d+x\d+", ep_slug)
    return m.group(1) if m else None
