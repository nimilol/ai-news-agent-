"""Fetchers: one function per source type. Each returns a list of items (see common.make_item).

`state` is a small dict kept between runs for this source, for fetchers that need memory
(page_diff remembers which paragraphs it has seen, youtube caches the channel id).
"""

import calendar
import hashlib
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


def _page_blocks(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "svg", "form"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    blocks = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        if el.find(["p", "li"]):  # skip containers, keep the leaf text
            continue
        text = clean_text(el.get_text(" ", strip=True))
        if len(text) >= 20:
            blocks.append((el.name, text))
    return blocks


def fetch_page_diff(source, state):
    """A release-notes page: paragraphs not seen on earlier runs are reported as changes."""
    blocks = _page_blocks(get(source["url"]).text)
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


def fetch_openrouter_models(source, state):
    """New model ids on OpenRouter from our companies, plus anonymous 'stealth' models."""
    items = []
    for m in get("https://openrouter.ai/api/v1/models").json().get("data", []):
        model_id = m.get("id", "")
        if ":" in model_id:  # skip ':free' / ':beta' duplicates of a model
            continue
        company = next((c for prefix, c in OPENROUTER_COMPANIES.items() if model_id.startswith(prefix)), None)
        stealth = model_id.startswith("openrouter/") or "stealth" in (m.get("name", "") + model_id).lower()
        if not company and not stealth:
            continue
        price = m.get("pricing", {})
        summary = (
            f"{clean_text(m.get('description', ''), 500)} Context: {m.get('context_length', '?')} tokens. "
            f"Price per token: input {price.get('prompt', '?')}, output {price.get('completion', '?')}."
        )
        if stealth:
            summary = "Anonymous/stealth model (often an unreleased model being tested). " + summary
        item = make_item(source, f"OpenRouter lists new model: {m.get('name', model_id)} ({model_id})",
                         f"https://openrouter.ai/{model_id}", summary, uid=model_id)
        if company:
            item["companies"] = [company]
        items.append(item)
    return items


def fetch_youtube(source, state):
    channel_id = source.get("channel_id") or state.get("channel_id")
    if not channel_id:
        page = get(f"https://www.youtube.com/@{source['handle']}").text
        found = re.search(r'channel_id=(UC[\w-]{22})', page) or re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', page)
        if not found:
            raise ValueError(f"could not find the channel id for @{source['handle']}")
        channel_id = state["channel_id"] = found.group(1)
    return _feed_items(source, get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}").content)


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
        if lower.get(sub.lower()):
            item["companies"] = [lower[sub.lower()]]
        items.append(item)
    return items


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

