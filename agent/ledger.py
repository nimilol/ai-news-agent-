"""What has already reached you, across every run (state/sent.json).

Each run keeps its own memory of what it has *seen*; this ledger records what was actually *sent*, so one story
isn't delivered again by another run: the release monitor's full alert is not repeated as an instant alert or as a
digest story, the same announcement from three sources is one alert, and Buzz doesn't re-pick a model repository
that was just announced.

Keys are fingerprints: a normalised link ("url:"), a normalised headline ("title:"), or a model identity
("model:<company>:<base key>"). Entries expire after KEEP_DAYS.
"""

import json
import re
from datetime import timedelta

from .common import now_utc, parse_date

KEEP_DAYS = 14
PATH_NAME = "sent.json"
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "its", "new", "now", "by", "at",
         "as", "from", "how", "what", "why", "this", "that", "be", "are", "it"}


def url_key(url):
    from .releases.identity import url_fingerprint

    return "url:" + url_fingerprint(url) if url else None


def title_words(title):
    words = re.findall(r"[a-z0-9][a-z0-9.\-]*", (title or "").lower())
    return {w.strip(".-") for w in words if w not in _STOP and (len(w) > 1 or w.isdigit())}


def title_key(title):
    words = sorted(title_words(title))
    return "title:" + " ".join(words) if words else None


def similar(a, b, threshold=0.6):
    """Two headlines about the same thing (word overlap). Used to merge alerts from several sources."""
    wa, wb = title_words(a), title_words(b)
    if not wa or not wb:
        return False
    na, nb = set(re.findall(r"\d+(?:\.\d+)?", a or "")), set(re.findall(r"\d+(?:\.\d+)?", b or ""))
    if na and nb and na.isdisjoint(nb):
        return False  # "Grok 5 ..." and "Grok 6 ..." are different stories
    return len(wa & wb) / min(len(wa), len(wb)) >= threshold


def model_keys(text, companies=None):
    """'model:<company>:<base>' for every model of the six monitored labs named in `text`."""
    from .releases import config as rconfig
    from .releases import identity

    companies = companies or rconfig.load()["companies"]
    keys = []
    for key, c in companies.items():
        for name in identity.extract_models(text or "", c["_patterns"]):
            base = identity.base_key(name, c)
            if base:
                keys.append(f"model:{key}:{base}")
    return list(dict.fromkeys(keys))


class Ledger:
    def __init__(self, root=None):
        from . import state  # the same state folder as every other memory file (tests point it elsewhere)

        self.path = (root or state.ROOT) / "state" / PATH_NAME
        raw = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        self._original = raw
        self.entries = json.loads(raw).get("sent", {}) if raw else {}

    def sent(self, keys, days=KEEP_DAYS):
        """The first matching entry sent within `days`, or None."""
        cutoff = now_utc() - timedelta(days=days)
        for k in keys:
            entry = self.entries.get(k) if k else None
            if entry and (parse_date(entry["at"]) or cutoff) >= cutoff:
                return entry
        return None

    def record(self, keys, title, channel, priority=None):
        at = now_utc().replace(microsecond=0).isoformat()
        for k in keys:
            if k:
                self.entries[k] = {"at": at, "title": (title or "")[:160], "channel": channel,
                                   **({"priority": priority} if priority else {})}

    def save(self):
        cutoff = now_utc() - timedelta(days=KEEP_DAYS)
        self.entries = {k: v for k, v in self.entries.items() if (parse_date(v.get("at")) or cutoff) >= cutoff}
        text = json.dumps({"sent": self.entries}, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        if text != self._original:
            self.path.parent.mkdir(exist_ok=True)
            self.path.write_text(text, encoding="utf-8")
            self._original = text
