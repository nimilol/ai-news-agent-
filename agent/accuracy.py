"""Accuracy helpers: block low-quality publishers, read full articles, count independent sources,
first-seen times, and remember recent headlines so the same story isn't reported twice."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlsplit

from .common import clean_text, get, now_utc, parse_date

# Links that already carry their full content in the feed, or can't be read as articles.
NO_ARTICLE_HOSTS = ("news.google.com", "reddit.com", "huggingface.co", "openrouter.ai", "models.dev",
                    "github.com", "youtube.com", "youtu.be", "x.com", "twitter.com")
RECENT_HOURS = 48


def publisher(item):
    """Who actually published it: the ' - Publisher' suffix on Google News titles, else the link's domain."""
    if item["source"].startswith("Google News") and " - " in item["title"]:
        return item["title"].rsplit(" - ", 1)[1].strip()
    host = urlsplit(item["url"]).netloc.lower()
    return host[4:] if host.startswith("www.") else host or item["source"]


def is_blocked(item, blocked):
    if not blocked:
        return False
    name = publisher(item).lower()
    host = urlsplit(item["url"]).netloc.lower()
    return any(b in name or host.endswith(b) for b in blocked)


def _article_text(url, chars):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(get(url, timeout=12).text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "form", "figure"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    paragraphs = [clean_text(p.get_text(" ", strip=True)) for p in root.find_all("p")]
    text = " ".join(p for p in paragraphs if len(p) > 40)
    return clean_text(text, chars)


def add_articles(items, max_articles=15, chars=2500):
    """Read the full article for the top items, so the AI summarises the story, not just the headline."""
    targets = [it for it in items if it["url"] and not any(h in urlsplit(it["url"]).netloc for h in NO_ARTICLE_HOSTS)]
    targets = targets[:max_articles]

    def read(item):
        try:
            text = _article_text(item["url"], chars)
            if len(text) > 200:
                item["article"] = text
        except Exception:  # noqa: BLE001 - paywalls, timeouts: keep the headline and summary
            pass

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(read, targets))
    read_count = sum(1 for it in targets if it.get("article"))
    print(f"  Read {read_count} of {len(targets)} full articles.")
    return read_count


def source_count(story, items):
    """Independent publishers behind a story (two copies of the same outlet count once)."""
    return len({publisher(items[n - 1]).lower() for n in story["items"]})


def first_seen(story, items):
    dates = [parse_date(items[n - 1]["published"]) for n in story["items"]]
    dates = [d for d in dates if d]
    return min(dates) if dates else None


def enforce_status(story, items):
    """A story is only 'official' if one of its items comes from the company's own official source."""
    if story["status"] == "official" and not any(items[n - 1]["tier"] == "official" for n in story["items"]):
        tiers = {items[n - 1]["tier"] for n in story["items"]}
        story["status"] = "rumor" if tiers <= {"leak", "community"} else "report"


def badges(story, items, now=None):
    """Short trust line: how many sources, when it first appeared, whether it's an update."""
    now = now or now_utc()
    parts = []
    if story.get("update"):
        parts.append("🔄 update")
    count = source_count(story, items)
    if count >= 2:
        parts.append(f"{count} sources")
    elif story["status"] != "official":
        parts.append("⚠️ single source")
    seen = first_seen(story, items)
    if seen:
        hours = (now - seen).total_seconds() / 3600
        parts.append(f"🕐 {seen:%H:%M} UTC" if hours < 20 else f"🕐 {seen:%d %b}")
    return " · ".join(parts)


def recent_headlines(state):
    cutoff = (now_utc() - timedelta(hours=RECENT_HOURS)).isoformat()
    stories = [s for s in state.source("_stories").get("recent", []) if s["d"] >= cutoff]
    state.source("_stories")["recent"] = stories
    return [s["t"] for s in stories]


def remember_headlines(state, brief):
    memory = state.source("_stories")
    now = now_utc().isoformat()
    memory["recent"] = memory.get("recent", []) + [{"t": s["title"], "d": now} for s in brief["stories"]]
