"""/modelaccess: the replies, built once per refresh into a "bundle" so both bots answer the same way.

The hourly Python bot builds the bundle from the database and routes with `route()`. The Cloudflare bot gets the
same bundle (pushed to its storage in a few pieces) and routes with the identical logic in bot/src/worker.js.
Commands therefore answer from stored data instantly; fresh research only happens on the scheduled refresh, or
in the Python bot when the database has never been built.
"""

import hashlib
import json
import re

from . import analyze, render
from .normalize import norm

SHARDS = 16
VIEWS = ("free", "cheapest", "leaderboard", "weekly", "help")
BRANDS = {"claude": "anthropic", "gpt": "openai", "chatgpt": "openai", "codex": "openai", "gemini": "google",
          "gemma": "google", "grok": "xai", "llama": "meta", "qwen": "alibaba", "kimi": "moonshotai", "glm": "zhipuai",
          "mimo": "xiaomi", "nemotron": "nvidia", "phi": "microsoft", "jamba": "ai21", "ernie": "baidu",
          "seed": "bytedance", "doubao": "bytedance", "longcat": "meituan", "devstral": "mistral",
          "codestral": "mistral", "magistral": "mistral", "muse": "meta", "inkling": "thinkingmachines"}


def brands(cfg):
    out = dict(BRANDS)
    for key, c in (cfg.get("companies") or {}).items():
        out[key] = key
        name = (c or {}).get("name") or ""
        first = norm(name.split(" ")[0]) if name else ""
        if first:
            out.setdefault(first, key)
        for a in (c or {}).get("aliases") or []:
            out.setdefault(norm(a), key)
    return out


def model_keys(m):
    slug = m["id"].split("/", 1)[1]
    keys = {norm(slug), norm(m.get("name"))}
    return sorted(k for k in keys if k)


def find(index, query, company=None):
    """Same matching as worker.js findModel: exact key, then '…-query', then 'query-…', then every word present.
    `index` is in rank order, so the first hit is the best-known model."""
    q = norm(query)
    if not q:
        return None
    words = q.split("-")
    rows = [r for r in index if not company or r["c"] == company]
    tests = [lambda k: k == q, lambda k: k.endswith("-" + q), lambda k: k.startswith(q + "-"),
             lambda k: all(w in k.split("-") for w in words)]
    for test in tests:
        for r in rows:
            if any(test(k) for k in r["k"]):
                return r
    return None


def shard_of(mid):
    return int(hashlib.sha1(mid.encode()).hexdigest(), 16) % SHARDS


def build(db, cfg):
    settings = cfg["settings"]
    views = analyze.all_views(db, settings)
    board = analyze.leaderboard(views, settings)
    by_cat = analyze.cheapest_by_category(views)
    order = analyze.rank_order(views)
    index, shards = [], [{} for _ in range(SHARDS)]
    for mid in order:
        v = views[mid]
        s = shard_of(mid)
        index.append({"i": mid, "k": model_keys(v["model"]), "c": v["model"]["company"], "s": s})
        shards[s][mid] = {"card": render.render_model(v, db, cfg), "compare": render.render_compare(v, db, cfg),
                          "alternatives": render.render_alternatives(v, analyze.alternatives(views, mid), db, cfg)}
    companies = {}
    for key in sorted({v["model"]["company"] for v in views.values()}):
        companies[key] = render.render_company(key, views, db, cfg)
    bundle = {
        "updated": db.meta.get("last_refresh"),
        "views": {"overview": render.render_overview(db, views, board, cfg), "free": render.render_free(db, views, cfg),
                  "cheapest": render.render_cheapest(db, views, by_cat, board, cfg),
                  "leaderboard": render.render_leaderboard(board, db, cfg), "help": render.render_help(),
                  "weekly": last_weekly(), "empty": render.render_empty(),
                  "not_found": render.render_not_found("{query}", db, cfg)},
        "brands": brands(cfg), "companies": companies, "index": index, "shards": shards,
    }
    return bundle, views, board


def last_weekly():
    from .db import weekly_path

    path = weekly_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return [f"{render.PREFIX}\nNo Weekly Model Access Report yet. It arrives on Saturday afternoon."]


def route(bundle, text):
    """The reply (list of messages) for '/modelaccess <text>'. Identical logic to worker.js routeModelAccess."""
    if not bundle or not bundle.get("index"):
        return (bundle or {}).get("views", {}).get("empty") or render.render_empty()
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    views = bundle["views"]
    if not text:
        return views["overview"]
    words = text.split(" ")
    first, rest = words[0].lower(), " ".join(words[1:]).strip()
    if first in VIEWS and not rest:
        return views[first]
    if first in ("compare", "alternatives", "alternative", "alt"):
        if not rest:
            return [f"{render.PREFIX}\nName a model, e.g. <i>/modelaccess {first} kimi k3</i>"]
        hit = find(bundle["index"], rest)
        if not hit:
            return not_found(bundle, rest)
        entry = bundle["shards"][hit["s"]][hit["i"]]
        return entry["compare"] if first == "compare" else entry["alternatives"]
    company = bundle["brands"].get(norm(first))
    if company:
        if not rest:
            return bundle["companies"].get(company) or not_found(bundle, text)
        hit = find(bundle["index"], text, company) or find(bundle["index"], rest, company) or find(bundle["index"], text)
    else:
        hit = find(bundle["index"], text)
    if not hit:
        return not_found(bundle, text)
    return bundle["shards"][hit["s"]][hit["i"]]["card"]


def not_found(bundle, query):
    import html

    return [m.replace("{query}", html.escape(query)) for m in bundle["views"]["not_found"]]


def bot_payloads(bundle):
    """The pieces pushed to the Cloudflare bot: small ones (views, index) and the per-model shards."""
    head = {k: bundle[k] for k in ("updated", "views", "brands", "companies")}
    index = [dict(r) for r in bundle["index"]]
    out = {"modelaccess_main": head, "modelaccess_index": index}
    for n, shard in enumerate(bundle["shards"]):
        out[f"modelaccess_s{n}"] = shard
    return out


def payload_hash(value):
    return hashlib.sha1(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
