"""Instant alerts (hourly, no AI) in the standard message structure.

Each new item from an alert source gets a section, a priority, a verification label and a short, honest
"why it matters" derived from where it came from. Then:
  * minor edits to documentation pages (no price, limit, model or deprecation change) are dropped,
  * items the release monitor or an earlier alert already delivered are dropped (state/sent.json),
  * several sources reporting the same thing become one card with several links,
  * P3 discoveries (e.g. new free models) wait for the digest instead of pinging you.
"""

import re

from . import ledger as led
from . import messages as msg
from .accuracy import publisher

RELEASE_WORDS = re.compile(r"\b(introduc\w*|launch\w*|releas\w*|unveil\w*|now available|generally available|"
                           r"new model)\b", re.I)
DOCS_WORDS = re.compile(r"\b(pric\w*|deprecat\w*|retir\w*|sunset\w*|limits?|plans?)\b", re.I)
SIGNIFICANT = re.compile(r"\$\s?\d|\bpric\w*|\bdeprecat\w*|\bretir\w*|\bsunset\w*|shut\w* down|\bnew model|"
                         r"\blaunch\w*|\bintroduc\w*|now available|generally available|context window|rate limits?|"
                         r"\b\d+[kKmM]? tokens\b|\bend of life\b", re.I)
MODEL_HINT = re.compile(r"\b(claude|opus|sonnet|haiku|gpt-?\d|o\d\b|grok|glm-?\d|gemini|kimi|qwen|model)\b", re.I)


def classify_item(item, source, companies):
    """(section, priority, verification, why) for one alert item, or None for a minor docs edit."""
    title = item["title"]
    if title.startswith(item["source"] + ":"):  # page changes are titled "<page name>: <section>"
        title = title[len(item["source"]) + 1:]
    text = f"{title} {item['summary']}"
    company = companies.get((item["companies"] or ["industry"])[0], {}).get("label", "the company")
    if "free_models" in item["companies"]:
        return ("free", "P3", "✅ Listed by the provider",
                "Free to use right now (rate-limited), useful for testing and for viewers on a budget.")
    if source.get("tables"):  # pages companies edit quietly: models, prices, deprecations, plans
        if not SIGNIFICANT.search(text):
            return None
        return ("model_updates", "P2", "✅ Official page change",
                f"An unannounced change on {company}'s own page; prices, limits and model lists often change here first.")
    if source.get("type") == "huggingface":
        return ("model_updates", "P2", "✅ Official repository",
                f"A new repository from {company}'s own Hugging Face organisation; open weights often appear here first.")
    tier = item["tier"]
    if tier == "official":
        if RELEASE_WORDS.search(item["title"]) and MODEL_HINT.search(item["title"]):
            return ("new_models", "P1", "✅ Official",
                    f"An official announcement from {company}. New models are verified and compared in a separate alert.")
        if DOCS_WORDS.search(item["title"]) or source.get("type") == "page_diff":
            return ("model_updates", "P2", "✅ Official",
                    f"An official change from {company}'s release notes.")
        return ("industry", "P2", "✅ Official", f"Official news from {company}.")
    if tier == "leak":
        return ("industry", "P2", "👀 Early signal (unconfirmed)",
                "Leaks and new listings often appear days before an official announcement. Not confirmed yet.")
    return ("industry", "P2", "📰 Report", "Reported by an outside source, not by the company itself.")


def item_keys(item):
    return [led.url_key(item["url"]), led.title_key(item["title"])]


def already_sent(ledger, item, model_keys):
    """Delivered before: the same link or headline, a model the release monitor already announced, or a near-
    identical headline about the same model in the last 3 days (the same leak from another source)."""
    if ledger.sent(item_keys(item)):
        return True
    entry = ledger.sent(model_keys, days=3)
    return bool(entry and (entry.get("channel") == "release" or led.similar(entry["title"], item["title"])))


def build_cards(items, sources_by_name, companies, ledger, release_companies=None, min_priority="P2"):
    """Cards for the alert message, plus the items that were held back (for logging). Returns (cards, kept, held)."""
    cards, kept, held = [], [], []
    for it in items:
        source = sources_by_name.get(it["source"], {})
        got = classify_item(it, source, companies)
        if got is None:
            held.append((it, "minor documentation edit"))
            continue
        section, priority, verification, why = got
        models = led.model_keys(it["title"], release_companies)
        if already_sent(ledger, it, models):
            held.append((it, "already sent"))
            continue
        if msg.PRIORITY_RANK[priority] > msg.PRIORITY_RANK[min_priority]:
            held.append((it, f"{priority}: waits for the digest"))
            continue
        twin = next((c for c in cards if (set(models) & set(c["_models"])) or led.similar(it["title"], c["headline"])),
                    None)
        if twin:  # the same story from another source: one card, several links
            twin["sources"].append((publisher(it), it["url"]))
            twin["_items"].append(it)
            if msg.PRIORITY_RANK[priority] < msg.PRIORITY_RANK[twin["priority"]]:
                twin["priority"] = priority
            kept.append(it)
            continue
        label = companies.get((it["companies"] or ["industry"])[0], {}).get("label")
        c = msg.card(section, priority, it["title"], company=label, what=it["summary"] or None, why=why,
                     details=[f"via {it['source']}"], sources=[(publisher(it), it["url"])],
                     date=(it.get("published") or "")[:10] or None, verification=verification)
        c["_models"], c["_items"] = models, [it]
        cards.append(c)
        kept.append(it)
    for c in cards:
        if len(c["_items"]) > 1:
            c["details"] = [f"reported by {len(c['_items'])} sources"]
    return cards, kept, held


def record(ledger, cards):
    for c in cards:
        for it in c["_items"]:
            ledger.record(item_keys(it) + c["_models"], c["headline"], "alerts", c["priority"])
