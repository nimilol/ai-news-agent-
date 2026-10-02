"""Shared helpers: config loading, HTTP, text cleanup and the news item shape."""

import hashlib
import html
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 ai-news-agent/1.0"
)
TIER_RANK = {"official": 0, "leak": 1, "community": 2, "newsletter": 3, "press": 4}

_session = None


def load_yaml(name):
    with open(ROOT / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    return _session


def get(url, timeout=25, **kw):
    r = session().get(url, timeout=timeout, **kw)
    r.raise_for_status()
    return r


def now_utc():
    return datetime.now(timezone.utc)


def env(name):
    value = os.environ.get(name, "").strip()
    return value or None


def clean_text(text, limit=None):
    """Strip HTML tags and collapse whitespace."""
    if not text:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


_TRACKING = re.compile(r"^(utm_|ref$|ref_src$|fbclid$|gclid$|mc_cid$|mc_eid$)")


def normalize_url(url):
    """Drop tracking parameters and fragments-only noise so the same story isn't counted twice."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)])
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, query, parts.fragment))


def item_id(*parts):
    return hashlib.sha1("|".join(p or "" for p in parts).encode("utf-8")).hexdigest()[:16]


def make_item(source, title, url, summary="", published=None, uid=None):
    """One news item. `published` is an aware datetime or None when the source has no dates."""
    url = normalize_url(url) if url else ""
    return {
        "id": item_id(source["name"], uid or url or title),
        "title": clean_text(title, 300) or "(untitled)",
        "url": url,
        "summary": clean_text(summary, 1200),
        "published": published.isoformat() if published else None,
        "source": source["name"],
        "tier": source.get("tier", "newsletter"),
        "companies": [source["company"]] if source.get("company") else [],
    }


def parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
