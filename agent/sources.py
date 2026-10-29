"""Fetchers: one function per source type. Each returns a list of items (see common.make_item).

`state` is a small dict kept between runs for this source, for fetchers that need memory
(page_diff remembers which paragraphs it has seen, youtube caches the channel id).
"""

import calendar
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin

import feedparser
from bs4 import BeautifulSoup

from .common import clean_text, get, make_item, now_utc


def _struct_time(value):
    if not value:
        return None
    return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)  # feedparser times are UTC


def _feed_items(source, content):
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"not a valid feed ({feed.bozo_exception})")
    items = []
    for e in feed.entries:
        summary = e.get("summary", "")
        if not summary and e.get("content"):
            summary = e.content[0].get("value", "")
        if not summary and e.get("media_description"):
            summary = e.media_description
        published = _struct_time(e.get("published_parsed") or e.get("updated_parsed"))
        items.append(make_item(source, e.get("title", ""), e.get("link", ""), summary, published, uid=e.get("id")))
    return items


def fetch_rss(source, state):
    return _feed_items(source, get(source["url"]).content)


GENERIC_LINK_TEXT = re.compile(r"^(read|learn|see|view|find out)( more)?|^more$|^continue", re.I)


def fetch_page_links(source, state):
    """A news page without a feed: every link whose path matches `pattern` is a post."""
    page = get(source["url"])
    soup = BeautifulSoup(page.text, "html.parser")
    pattern = re.compile(source["pattern"])
    posts = {}  # url -> {"titles": [...], "pieces": [...]}, in page order
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0].split("?")[0]
        if not pattern.search(href):
            continue
        post = posts.setdefault(urljoin(source["url"], href), {"titles": [], "pieces": []})
        # A card's title is usually a heading or an element with "title" in its class.
        for el in a.find_all(["h1", "h2", "h3", "h4"]) + a.find_all(class_=re.compile("title", re.I)):
            text = clean_text(el.get_text(" ", strip=True))
            if text and not GENERIC_LINK_TEXT.match(text):
                post["titles"].append(text)
        post["pieces"] += [s.strip() for s in a.stripped_strings
                           if len(s.strip()) > 3 and not GENERIC_LINK_TEXT.match(s.strip())]
        if a.get("aria-label"):
            post["pieces"].append(a["aria-label"])
    items = []
    for url, post in posts.items():
        pieces = list(dict.fromkeys(post["pieces"]))
        title = (post["titles"] or [None])[0] or (max(pieces, key=len) if pieces else "")
        if not title:
            title = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").capitalize()
        items.append(make_item(source, title, url, " · ".join(p for p in pieces if p != title)[:300]))
    if not items:
        raise ValueError("no matching links found (page layout changed or content is loaded by JavaScript)")
    return items


def _page_blocks(html_text, tables=False):
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "svg", "form"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    names = ["h1", "h2", "h3", "h4", "p", "li"] + (["tr", "dt", "dd"] if tables else [])
    blocks = []
    for el in root.find_all(names):
        if el.find(["p", "li"]):  # skip containers, keep the leaf text
            continue
        text = clean_text(el.get_text(" ", strip=True))
        if len(text) >= (12 if el.name in ("tr", "dt", "dd") else 20):
            blocks.append((el.name, text))
    return blocks


def fetch_page_diff(source, state):
    """A release-notes page: paragraphs not seen on earlier runs are reported as changes."""
    blocks = _page_blocks(get(source["url"]).text, tables=source.get("tables", False))
    if len(blocks) < 3:
        raise ValueError("page has almost no readable text (blocked, or loaded by JavaScript)")
    known = set(state.get("hashes", []))
    hashes = [hashlib.sha1(text.encode()).hexdigest()[:12] for _, text in blocks]
    new_idx = [i for i, h in enumerate(hashes) if h not in known]
    first_run = not known
    # Keep everything seen recently, so a paragraph that disappears and comes back isn't "new".
    state["hashes"] = list(dict.fromkeys(hashes + state.get("hashes", [])))[:4000]
    if first_run or not new_idx:
        return []
    if len(new_idx) > 0.6 * len(blocks):
        print(f"  [{source['name']}] page layout changed; re-learning it without reporting")
        return []
    # Group neighbouring new paragraphs into one change each.
    groups, current = [], []
    for i in new_idx:
        if current and i != current[-1] + 1:
            groups.append(current)
            current = []
        current.append(i)
    if current:
        groups.append(current)
    items = []
    for g in groups[:5]:
        texts = [blocks[i][1] for i in g]
        heading = next((blocks[i][1] for i in g if blocks[i][0].startswith("h")), None)
        if heading is None:  # look upward for the section heading this change sits under
            heading = next((blocks[j][1] for j in range(g[0] - 1, -1, -1) if blocks[j][0].startswith("h")), "")
        title = f"{source['name']}: {heading or texts[0]}"
        items.append(make_item(source, title, source["url"], " ".join(texts), now_utc(), uid=hashes[g[0]]))
    return items


def fetch_huggingface(source, state):
    url = f"https://huggingface.co/api/models?author={source['author']}&sort=createdAt&direction=-1&limit=20"
    items = []
    for m in get(url).json():
        model_id = m.get("id") or m.get("modelId")
        summary = f"New model repository {model_id} (created {m.get('createdAt', '?')[:10]})."
        if m.get("pipeline_tag"):
            summary += f" Task: {m['pipeline_tag']}."
        items.append(make_item(source, f"New on Hugging Face: {model_id}", f"https://huggingface.co/{model_id}", summary, uid=model_id))
    return items


OPENROUTER_COMPANIES = {"anthropic/": "anthropic", "openai/": "openai", "x-ai/": "xai", "z-ai/": "zhipu", "thudm/": "zhipu"}


def _is_free(price):
    try:
        return float(price.get("prompt", 1)) == 0 and float(price.get("completion", 1)) == 0
    except (TypeError, ValueError):
        return False


def fetch_openrouter_models(source, state):
    """New models on OpenRouter.
    Default: models from our companies, plus anonymous 'stealth' models (often unreleased models in testing).
    mode: free -> every new model you can use for free, from any company."""
    free_mode = source.get("mode") == "free"
    items = []
    for m in get("https://openrouter.ai/api/v1/models").json().get("data", []):
        model_id, name = m.get("id", ""), m.get("name", m.get("id", ""))
        price = m.get("pricing", {})
        context = f"Context: {m.get('context_length', '?')} tokens."
        if free_mode:
            if not _is_free(price):
                continue
            summary = f"Free to use on OpenRouter (rate-limited). {context} {clean_text(m.get('description', ''), 400)}"
            item = make_item(source, f"New free model on OpenRouter: {name} ({model_id})",
                             f"https://openrouter.ai/{model_id}", summary, uid=model_id)
            item["companies"] = ["free_models"]
            items.append(item)
            continue
        if ":" in model_id:  # skip ':free' / ':beta' duplicates of a model
            continue
        company = next((c for prefix, c in OPENROUTER_COMPANIES.items() if model_id.startswith(prefix)), None)
        stealth = model_id.startswith("openrouter/") or "stealth" in (name + model_id).lower()
        if not company and not stealth:
            continue
        summary = (f"{clean_text(m.get('description', ''), 500)} {context} "
                   f"Price per token: input {price.get('prompt', '?')}, output {price.get('completion', '?')}.")
        if stealth:
            summary = "Anonymous/stealth model (often an unreleased model being tested). " + summary
        item = make_item(source, f"OpenRouter lists new model: {name} ({model_id})",
                         f"https://openrouter.ai/{model_id}", summary, uid=model_id)
        if company:
            item["companies"] = [company]
        items.append(item)
    return items


def fetch_models_dev(source, state):
    """models.dev: the open model database behind OpenCode. Models often appear here as soon as a provider lists them.
    `providers` maps a models.dev provider id to a company (or to free_models).
    free_only: true -> only models that cost nothing (e.g. OpenCode's free models)."""
    data = get("https://models.dev/api.json").json()
    items = []
    for provider_id, company in source["providers"].items():
        provider = data.get(provider_id)
        if not isinstance(provider, dict):
            continue
        pname = provider.get("name", provider_id)
        for model_id, m in (provider.get("models") or {}).items():
            if not isinstance(m, dict):
                continue
            cost = m.get("cost") or {}
            free = cost.get("input", 1) == 0 and cost.get("output", 1) == 0
            if source.get("free_only") and not free:
                continue
            name = m.get("name") or model_id
            limit = (m.get("limit") or {}).get("context", "?")
            summary = (f"Listed by {pname} on models.dev. Released: {m.get('release_date', '?')}. Context: {limit} tokens. "
                       f"Cost per million tokens: input {cost.get('input', '?')}, output {cost.get('output', '?')}.")
            if source.get("free_only"):
                title = f"New free model in {pname}: {name} ({model_id})"
            else:
                title = f"New model listed for {pname}: {name} ({model_id})"
            item = make_item(source, title, f"https://models.dev/?search={quote(model_id)}", summary,
                             uid=f"{provider_id}/{model_id}")
            if company:
                item["companies"] = [company]
            items.append(item)
    if not items and not source.get("free_only"):
        raise ValueError("no models found (models.dev layout changed?)")
    return items


# Interviews, podcasts and AMAs with people who work in AI (founders, CEOs, researchers, engineers).
INTERVIEW = re.compile(
    r"\binterview|\bpodcast\b|\bAMA\b|ask me anything|in conversation|conversation with|fireside|"
    r"sits? down with|sat down with|\bin discussion\b|\bQ&A\b|\bkeynote\b|\btestif(y|ies|ied)\b|"
    r"\bep(isode)?\.? ?#?\d+\b|\bjoins (us|me)\b|\bwith (ceo|cto|founder|co-?founder)\b", re.I)


def is_interview(*texts):
    return any(INTERVIEW.search(t or "") for t in texts)


def youtube_channel_id(handle, cache):
    """A channel's id from its @handle (one page load), remembered in `cache` (handle -> id)."""
    if cache.get(handle):
        return cache[handle]
    page = get(f"https://www.youtube.com/@{handle}").text
    found = re.search(r'channel_id=(UC[\w-]{22})', page) or re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', page)
    if not found:
        raise ValueError(f"could not find the channel id for @{handle}")
    cache[handle] = found.group(1)
    return cache[handle]


def _num(d, key):
    try:
        return int((d or {}).get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _youtube_feed(channel_id):
    feed = feedparser.parse(get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}").content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"not a valid feed ({feed.bozo_exception})")
    out = []
    for e in feed.entries:
        out.append({"id": e.get("id") or f"yt:video:{e.get('yt_videoid')}", "title": e.get("title", ""),
                    "url": e.get("link", ""), "description": e.get("summary") or e.get("media_description") or "",
                    "channel": e.get("author") or feed.feed.get("title", ""),
                    "published": _struct_time(e.get("published_parsed") or e.get("updated_parsed")),
                    "views": _num(e.get("media_statistics"), "views"), "likes": _num(e.get("media_starrating"), "count")})
    return out


_AGO = re.compile(r"(\d+) (second|minute|hour|day|week|month|year)s? ago")
_VIEWS = re.compile(r"\b([\d.,]+)\s*(K|M|B|thousand|million|billion)?\s+views?\b", re.I)
_SCALE = {"": 1, "k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}
_UNIT_HOURS = {"second": 1 / 3600, "minute": 1 / 60, "hour": 1, "day": 24, "week": 168, "month": 730, "year": 8760}


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def _dicts(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _dicts(v)
    elif isinstance(o, list):
        for v in o:
            yield from _dicts(v)


def _text(t):
    if isinstance(t, str):
        return t
    if isinstance(t, dict):
        return t.get("simpleText") or t.get("content") or "".join(r.get("text", "") for r in t.get("runs", []))
    return ""


def parse_youtube_videos_page(page, channel=""):
    """The videos on a channel's /videos page (its ytInitialData), newest first. Dates are approximate
    ("2 days ago"). Handles both the older videoRenderer and the newer lockupViewModel layouts."""
    found = re.search(r"ytInitialData\"?\]?\s*=\s*(\{.+?\})\s*;\s*</script>", page, re.S)
    if not found:
        raise ValueError("no video list on the channel page")
    data = json.loads(found.group(1))
    now, out, seen = now_utc(), [], set()
    for d in _dicts(data):
        if "videoId" in d and "title" in d and ("viewCountText" in d or "publishedTimeText" in d):
            vid, title, desc = d["videoId"], _text(d["title"]), _text(d.get("descriptionSnippet"))
        elif d.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO" and d.get("contentId"):
            meta = (d.get("metadata") or {}).get("lockupMetadataViewModel") or {}
            vid, title, desc = d["contentId"], _text(meta.get("title")), ""
        else:
            continue
        if vid in seen or not title:
            continue
        seen.add(vid)
        views, published = 0, None
        for text in _strings(d):
            if text == title:  # a title can say "views" too
                continue
            m = _VIEWS.search(text)
            if m and not views:
                try:
                    views = int(float(m.group(1).replace(",", "")) * _SCALE[(m.group(2) or "").lower()])
                except ValueError:
                    pass
            a = _AGO.search(text)
            if a and not published:
                published = now - timedelta(hours=int(a.group(1)) * _UNIT_HOURS[a.group(2)])
        out.append({"id": f"yt:video:{vid}", "title": title, "url": f"https://www.youtube.com/watch?v={vid}",
                    "description": desc, "channel": channel, "published": published, "views": views, "likes": 0})
    if not out:
        raise ValueError("no videos found on the channel page")
    return out


def youtube_videos(channel_id, views=False):
    """The channel's latest videos.
    Reads the channel's public feed (exact dates, full descriptions) and, when YouTube refuses it (it sometimes
    answers 404 for every channel for days), the channel's Videos page instead.
    views=True reads the Videos page first: the feed's view counts are often missing (0)."""
    def page():
        return parse_youtube_videos_page(get(f"https://www.youtube.com/channel/{channel_id}/videos").text)
    readers = [("videos page", page), ("feed", lambda: _youtube_feed(channel_id))]
    errors = []
    for name, read in readers if views else readers[::-1]:
        try:
            return read()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {str(e)[:80]}")
    raise ValueError("; ".join(errors))


def fetch_youtube(source, state):
    """One YouTube channel. interview: true marks every video as an interview (podcast channels);
    otherwise a video counts as one when its title says so."""
    channel_id = source.get("channel_id") or state.get("channel_id")
    if not channel_id:
        channel_id = state["channel_id"] = youtube_channel_id(source["handle"], {})
    items = []
    for v in youtube_videos(channel_id):
        summary = v["description"]
        if v["views"]:
            summary = f"{v['views']:,} views on YouTube. {summary}"
        item = make_item(source, v["title"], v["url"], summary, v["published"], uid=v["id"])
        item["interview"] = bool(source.get("interview")) or is_interview(v["title"])
        items.append(item)
    return items


def fetch_hn(source, state):
    queries = source["query"] if isinstance(source["query"], list) else [source["query"]]
    since = int((now_utc() - timedelta(hours=48)).timestamp())
    items, seen = [], set()
    for q in queries:
        params = {"query": q, "tags": "story", "hitsPerPage": 30, "restrictSearchableAttributes": "title",
                  "numericFilters": f"points>={source.get('min_points', 30)},created_at_i>{since}"}
        for hit in get("https://hn.algolia.com/api/v1/search_by_date", params=params).json().get("hits", []):
            if hit["objectID"] in seen:
                continue
            seen.add(hit["objectID"])
            discussion = f"https://news.ycombinator.com/item?id={hit['objectID']}"
            summary = f"{hit.get('points', 0)} points, {hit.get('num_comments', 0)} comments. Discussion: {discussion}"
            published = datetime.fromtimestamp(hit["created_at_i"], tz=timezone.utc)
            items.append(make_item(source, hit.get("title", ""), hit.get("url") or discussion, summary, published,
                                   uid=hit["objectID"]))
    return items


def fetch_reddit(source, state):
    """All subreddits in ONE request (Reddit answers "429 Too Many Requests" to several in a row).
    `subreddits` maps each subreddit to a company, or to nothing for general ones."""
    subs = source["subreddits"]
    url = f"https://www.reddit.com/r/{'+'.join(subs)}/top/.rss?t=day&limit=100"
    feed = feedparser.parse(get(url).content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"not a valid feed ({feed.bozo_exception})")
    lower = {k.lower(): v for k, v in subs.items()}
    items = []
    for e in feed.entries:
        sub = next((t.get("term", "") for t in e.get("tags", [])), "")
        content = e.content[0].get("value", "") if e.get("content") else e.get("summary", "")
        item = make_item(source, e.get("title", ""), e.get("link", ""), content,
                         _struct_time(e.get("published_parsed") or e.get("updated_parsed")), uid=e.get("id"))
        item["source"] = f"r/{sub}" if sub else source["name"]
        item["interview"] = is_interview(item["title"])
        if lower.get(sub.lower()):
            item["companies"] = [lower[sub.lower()]]
        items.append(item)
    return items


def reddit_posts(subreddits, period="day", limit=50, query=None):
    """Top posts across several subreddits in one request, best first (the feed is ordered by upvotes).
    query: search those subreddits instead (e.g. 'interview OR AMA').
    Uses RSS: Reddit refuses its JSON API to GitHub's servers."""
    subs = "+".join(subreddits)
    params = {"t": period, "limit": limit}
    if query:
        params.update(q=query, restrict_sr="on", sort="top")
        url = f"https://www.reddit.com/r/{subs}/search.rss"
    else:
        url = f"https://www.reddit.com/r/{subs}/top/.rss"
    feed = feedparser.parse(get(url, params=params).content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"not a valid feed ({feed.bozo_exception})")
    posts = []
    for e in feed.entries:
        content = e.content[0].get("value", "") if e.get("content") else e.get("summary", "")
        posts.append({"id": e.get("id") or e.get("link", ""), "title": e.get("title", ""), "url": e.get("link", ""),
                      "sub": next((t.get("term", "") for t in e.get("tags", [])), ""), "text": _reddit_text(content),
                      "score": None, "comments": None,
                      "published": _struct_time(e.get("published_parsed") or e.get("updated_parsed"))})
    return posts


def _reddit_text(content):
    """A post's own text, without Reddit's "submitted by /u/x [link] [comments]" footer."""
    text = clean_text(content)
    return re.sub(r"\s*submitted by\s+/u/\S+.*$", "", text).strip()


def fetch_google_news(source, state):
    q = quote(f"{source['query']} when:1d")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    return _feed_items(source, get(url).content)


FETCHERS = {
    "rss": fetch_rss,
    "page_links": fetch_page_links,
    "page_diff": fetch_page_diff,
    "huggingface": fetch_huggingface,
    "openrouter_models": fetch_openrouter_models,
    "models_dev": fetch_models_dev,
    "youtube": fetch_youtube,
    "hn": fetch_hn,
    "reddit": fetch_reddit,
    "google_news": fetch_google_news,
}


def fetch(source, state):
    fetcher = FETCHERS.get(source.get("type"))
    if not fetcher:
        raise ValueError(f"unknown source type {source.get('type')!r}")
    return fetcher(source, state)

