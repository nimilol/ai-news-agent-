"""Looking models up by name in the official catalogs the release monitor keeps (for /compare).

"Claude Opus 5.5", "claude-opus-5-5", "opus 5.5" and "anthropic/claude-opus-5-5" all find the same model. The
same index (with pre-formatted spec cells) is pushed to the instant bot, which does the same simple matching."""

import re

from . import identity
from .analyze import spec_cells


def normalize(text):
    text = identity.VENDOR_PREFIX.sub("", str(text or "").strip().lower())
    text = re.sub(r"[\s_.]+", "-", text)
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "", text)).strip("-")


def model_index(store, companies):
    """[{id, name, company, status, specs, cells, keys, plain}] for every model in the official catalogs."""
    out = []
    for key, cfg in companies.items():
        for mid, rec in store.models(key).items():
            p = identity.parse(mid, cfg)
            name = rec.get("name") if rec.get("name") and rec.get("name") != mid else identity.display_name(mid)
            keys = {normalize(mid), normalize(name), identity.base_key(p)}
            out.append({"id": mid, "name": name, "company": key, "status": rec.get("status"),
                        "specs": rec.get("specs") or {}, "cells": spec_cells(rec.get("specs") or {}, rec.get("status")),
                        "keys": sorted(k for k in keys if k), "plain": not p["snapshot"] and not p["variants"]})
    return out


def find(index, query):
    """The best match for a model name, or None. Exact names first, then a name that ends with the query
    ("opus 5.5" → claude-opus-5-5); plain ids are preferred over snapshots and variants."""
    q = normalize(query)
    if not q:
        return None
    for test in (lambda k: k == q, lambda k: k.endswith("-" + q), lambda k: k.startswith(q + "-")):
        hits = [m for m in index if any(test(k) for k in m["keys"])]
        if hits:
            return min(hits, key=lambda m: (not m["plain"], len(m["id"])))
    return None


def split_pair(text):
    """'/compare A vs B' arguments → (A, B) or None."""
    parts = [p.strip() for p in re.split(r"\s+(?:vs\.?|versus|and|against)\s+|\s*,\s*|\s+\|\s+", text or "", flags=re.I)
             if p.strip()]
    return (parts[0], parts[1]) if len(parts) >= 2 else None


def compact_index(index):
    """What the instant bot needs for /compare (no raw specs)."""
    return [{k: m[k] for k in ("id", "name", "company", "cells", "keys", "plain")} for m in index]
