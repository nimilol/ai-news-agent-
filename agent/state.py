"""Memory between runs, stored as JSON in state/ and committed back to the repo by the workflow."""

import json
from datetime import timedelta

from .common import ROOT, now_utc

KEEP_DAYS = 30


class State:
    def __init__(self, name):
        self.path = ROOT / "state" / f"{name}.json"
        self._original = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        data = json.loads(self._original) if self._original else {}
        self.seen = data.get("seen", {})          # item id -> date last observed
        self.sources = data.get("sources", {})    # source name -> fetcher memory
        self.failures = data.get("failures", {})  # source name -> failed runs in a row

    def source(self, name):
        return self.sources.setdefault(name, {})

    def is_new_source(self, name):
        return not self.sources.get(name, {}).get("initialized")

    def mark_initialized(self, name):
        self.source(name)["initialized"] = True

    def observe(self, item_id):
        """Returns True when the item was never seen before. Refreshes its date either way, so items
        that stay on a page for months are never forgotten and re-reported."""
        new = item_id not in self.seen
        self.seen[item_id] = now_utc().date().isoformat()
        return new

    def record_result(self, name, ok):
        if ok:
            self.failures.pop(name, None)
        else:
            self.failures[name] = self.failures.get(name, 0) + 1

    def save(self):
        cutoff = (now_utc() - timedelta(days=KEEP_DAYS)).date().isoformat()
        self.seen = {k: v for k, v in self.seen.items() if v >= cutoff}
        data = {"seen": self.seen, "sources": self.sources, "failures": self.failures}
        text = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        if text != self._original:  # only touch the file when something changed (fewer commits)
            self.path.parent.mkdir(exist_ok=True)
            self.path.write_text(text, encoding="utf-8")
            self._original = text
