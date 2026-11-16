"""Where the data comes from. Every fetcher retries transient failures and raises on a final failure, so the
pipeline can record which source failed and keep the last good data for it (never "free model disappeared"
because a catalog didn't load).

Research priority (highest first): official pricing pages and API docs (Gemini pricing page, provider limit docs,
platform plan pages) -> provider catalogs (models.dev, which mirrors official and third-party price lists, and
OpenRouter's live catalog) -> community leads (Reddit), which are only ever UNVERIFIED.
"""

import re
import time

from ..common import get

RETRY_WAITS = (2, 6)


def with_retries(fn, *args, waits=RETRY_WAITS, **kw):
    last = None
    for attempt in range(len(waits) + 1):
        try:
            return fn(*args, **kw)
        except Exception as e:  # noqa: BLE001 - retried, then raised
            last = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                break  # a 404/403 won't fix itself
            if attempt < len(waits):
                time.sleep(waits[attempt])
    raise last


def models_dev(url):
    data = with_retries(lambda: get(url, timeout=60).json())
    if not isinstance(data, dict) or len(data) < 10:
        raise ValueError("models.dev returned an unexpected shape")
    return data


def openrouter(url):
    data = with_retries(lambda: get(url, timeout=45).json())
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("OpenRouter returned no models")
    return rows


def openrouter_endpoints(model_id):
    """Every provider OpenRouter routes this model to, with its own prices (one request per model)."""
    data = with_retries(lambda: get(f"https://openrouter.ai/api/v1/models/{model_id}/endpoints", timeout=30).json(),
                        waits=(2,))
    return ((data or {}).get("data") or {}).get("endpoints") or []


def parse_gemini_pricing(text):
    """Google's Gemini API pricing page (markdown) -> {model code: {"name", "free", "improves_products"}}.
    "free" is True only when the section's input price in the Free Tier column reads "Free of charge"."""
    out = {}
    sections = re.split(r"\n(?=## )", text)
    for sec in sections:
        head = re.match(r"## \[?([^\]{\n]+?)\]?(?:\([^)]*\))?\s*(?:\{%[^\n]*)?\n", sec)
        if not head:
            continue
        codes = re.findall(r"\*\[`([a-z0-9][a-z0-9.\-]+)`\]", sec[:1500])
        if not codes:
            continue
        name = re.sub(r"\s*🍌", "", head.group(1)).strip()
        free = None
        m = re.search(r"\* Input price[^\n]*\n\s*\* ([^\n]+)", sec)
        if m:
            free = m.group(1).strip().lower().startswith("free of charge")
        elif re.search(r"not available on the free tier", sec, re.I):
            free = False
        else:
            free = "Free of charge" in sec
        if re.search(r"not available on the free tier", sec, re.I):
            free = False
        improve = None
        m = re.search(r"\* Used to improve our products\s*\n\s*\* \[?(Yes|No)", sec)
        if m:
            improve = m.group(1) == "Yes"
        for code in codes:
            out.setdefault(code, {"name": name, "free": bool(free), "improves_products": improve})
    return out


def gemini_pricing(url):
    text = with_retries(lambda: get(url, timeout=45).text)
    parsed = parse_gemini_pricing(text)
    if not parsed:
        raise ValueError("Gemini pricing page: no model sections found")
    return parsed


def check_page(url, patterns):
    """True when every regex pattern is found on the page (the platform's claim is still published)."""
    text = with_retries(lambda: get(url, timeout=40).text, waits=(3,))
    return all(re.search(p, text, re.I) for p in patterns)


def leads(lcfg):
    """Reddit posts that mention free or cheap access (one RSS request). Used only as UNVERIFIED leads."""
    from ..sources import reddit_posts

    subs = lcfg.get("subreddits") or []
    if not subs:
        return []
    return with_retries(reddit_posts, subs, "week", 50, lcfg.get("query") or '"free tier"', waits=(5,))
