"""The discovery run (`python -m agent discovery`, every 3 hours):

    1. deliver any batch from an earlier run that Telegram hasn't confirmed yet (and don't stack a new one on it)
    2. search the sources (GitHub searches are rotated so each runs about once a day; quotas are budgeted)
    3. drop what is irrelevant or already known (delivered, queued, recently rejected, waiting in the pool)
    4. evaluate the most promising new candidates (README read as untrusted text; licence, needs, security, quality)
    5. keep resources that pass in a pool, so good finds wait for a batch instead of being lost
    6. re-check delivered GitHub repositories for a meaningful new release (major version or real new features)
    7. pick the batch: best first, one entry per resource, a few per category; skip it if nothing is good enough
    8. explain each resource in plain language (AI, checked) and render the Telegram batch
    9. send it through the release monitor's Telegram outbox (only Telegram's confirmation counts as delivered)
   10. mark resources delivered with their Telegram message ids, and tell other runs (state/sent.json)
"""

import json
import re
from datetime import timedelta

from .. import botpush
from .. import ledger as led
from .. import messages as msg
from ..common import ROOT, load_yaml, now_utc, parse_date
from ..releases import notify
from . import config as dconfig
from . import evaluate as ev
from . import explain as ex
from . import identity, render
from . import sources as src
from .store import DiscoveryStore, now_iso, older_than

LAST_DIGEST = ROOT / "state" / "last_discovery.json"


# ----------------------------------------------------------------------------------------------- collecting

def _due(store, key, hours):
    last = (store.searches.get(key) or {}).get("last_run")
    return older_than(last, hours / 24)


def collect(cfg, store, gh):
    """All candidates from every enabled source. One source failing never stops the others."""
    sc, s = cfg["sources"], cfg["settings"]
    jobs = []
    g = sc.get("github", {})
    if g.get("enabled"):
        jobs.append(("GitHub search", lambda: src.github_search(gh, g.get("queries", []), store.searches,
                                                                s.get("queries_per_run", 8), g.get("min_hours", 20))))
        if g.get("orgs") and _due(store, "github:orgs", 12):
            jobs.append(("GitHub official orgs", lambda: src.github_orgs(gh, g["orgs"], g.get("org_days", 21)),
                         "github:orgs"))
        if g.get("trending"):
            jobs.append(("GitHub Trending", src.github_trending))
    if sc.get("huggingface", {}).get("enabled"):
        jobs.append(("Hugging Face", lambda: src.huggingface(sc["huggingface"])))
    if sc.get("mcp_registry", {}).get("enabled"):
        jobs.append(("MCP registry", lambda: src.mcp_registry(sc["mcp_registry"])))
    for name, fn in (("gitlab", src.gitlab), ("codeberg", src.codeberg)):
        cfg_x = sc.get(name, {})
        if cfg_x.get("enabled") and _due(store, name, cfg_x.get("every_hours", 24)):
            jobs.append((name.title(), lambda f=fn, c=cfg_x: f(c), name))
    out, failed = [], []
    for job in jobs:
        label, fn = job[0], job[1]
        try:
            got = fn()
            store.state.record_result(label, True)
            if len(job) > 2:
                store.searches[job[2]] = {"last_run": now_iso(), "results": len(got)}
            print(f"  ✓ {label}: {len(got)} candidates")
            out += got
        except Exception as e:  # noqa: BLE001
            store.state.record_result(label, False)
            failed.append(label)
            print(f"  ✗ {label}: {str(e)[:140]}")
    try:
        out = src.enrich_github(gh, out)
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ GitHub enrichment: {str(e)[:120]}")
        out = [c for c in out if not c.get("needs_enrichment")]
    return out, failed


def dedupe(candidates):
    """One candidate per resource (the same repository found by several searches keeps all its 'found by')."""
    by_id = {}
    for c in candidates:
        if not c.get("url"):
            continue
        c["url"] = identity.canonical_url(c["url"])
        c["rid"] = identity.resource_id(c)
        if c["rid"] in by_id:
            by_id[c["rid"]]["found_by"] = list(dict.fromkeys(by_id[c["rid"]]["found_by"] + c.get("found_by", [])))
        else:
            by_id[c["rid"]] = c
    return list(by_id.values())


def is_known(store, c, cfg):
    """Delivered, queued, waiting in the pool, or rejected recently: don't evaluate again."""
    rec = store.find(c)
    if not rec:
        return False
    if rec["status"] == "rejected":
        return not older_than(rec.get("last_checked"), cfg["settings"].get("recheck_rejected_days", 30))
    return rec["status"] in ("delivered", "queued", "pool")


def prerank(c):
    """Which new candidates to read first (READMEs cost time): official orgs, several sources, adoption."""
    import math

    return (bool(c.get("official_org")), len(c.get("found_by", [])),
            math.log10(1 + (c.get("stars") or 0) + (c.get("likes") or 0)))


def snapshot(c):
    keep = ("platform", "kind", "name", "display", "owner", "url", "description", "topics", "language", "stars", "forks",
            "open_issues", "pushed_at", "created_at", "license_spdx", "likes", "downloads", "default_branch",
            "node_id", "platform_id", "found_by", "official_org", "rid")
    return {k: c.get(k) for k in keep if c.get(k) not in (None, [], "")}


def source_group(c):
    if c.get("mcp"):
        return "mcp"
    if c.get("official_org"):
        return "org"
    if c["platform"] == "huggingface":
        return f"hf-{c.get('kind')}"
    return c["platform"] + ("-trending" if "GitHub Trending" in c.get("found_by", []) else "")


def interleave(candidates):
    """Best of each source in turn, so one busy source (e.g. a popular GitHub topic) can't use up the whole
    evaluation budget and every platform gets read."""
    groups = {}
    for c in sorted(candidates, key=prerank, reverse=True):
        groups.setdefault(source_group(c), []).append(c)
    out, queues = [], list(groups.values())
    while queues:
        out += [q.pop(0) for q in queues]
        queues = [q for q in queues if q]
    return out


def evaluate_new(cfg, store, gh, candidates):
    s = cfg["settings"]
    new = interleave([c for c in candidates if not is_known(store, c, cfg)])
    passed = 0
    for c in new[: s.get("evaluate_per_run", 30)]:
        readme = src.readme(c, s.get("max_readme_chars", 12000))
        if c.get("needs_license"):
            c["license_spdx"] = src.gitlab_license(c)
        e = ev.evaluate(c, readme, cfg)
        if e["reject"]:
            store.remember(c, status="rejected", reject_reason=e["reject"])
            print(f"    ✗ {c['name']}: {e['reject']}")
            continue
        if e["score"] < s.get("min_score", 45) or not e["priority"]:
            store.remember(c, status="rejected", reject_reason=f"score {e['score']} below the minimum", score=e["score"])
            continue
        if e["priority"] in ("P0", "P1") and c["platform"] == "github":
            e["contributors"] = src.github_contributors(gh, c["name"])
        passed += 1
        store.remember(c, status="pool", evaluated_at=now_iso(), score=e["score"], priority=e["priority"],
                       categories=e["categories"], licence_class=e["licence_class"], evaluation=e, snapshot=snapshot(c))
        print(f"    ✓ {c['name']}: {e['priority']} {e['score']} ({e['primary']}, {e['licence_class']})")
    return passed, len(new)


# ----------------------------------------------------------------------------------------------- meaningful updates

_SEMVER = re.compile(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def meaningful_update(old, new, release):
    """A major version (or the jump to 1.0), or a minor version whose notes describe real new features. Patch
    releases, routine commits and documentation changes are not meaningful."""
    a, b = _SEMVER.search(old or ""), _SEMVER.search(new or "")
    if not a or not b:
        return False
    (ma, mi), (mb, nb) = (int(a.group(1)), int(a.group(2) or 0)), (int(b.group(1)), int(b.group(2) or 0))
    if mb > ma:
        return True
    notes = f"{release.get('name', '')} {release.get('body', '')}"
    return mb == ma and nb > mi and len(release.get("body") or "") >= 300 and \
        bool(re.search(r"\b(new|add(ed|s)?|introduc\w*|now supports?|support for)\b", notes, re.I)) and \
        not re.search(r"\b(docs?|documentation|typo|readme)\b only", notes, re.I)


def check_updates(cfg, store, gh):
    s = cfg["settings"]
    due = [r for r in store.by_status("delivered") if r["platform"] == "github"
           and older_than(r.get("last_update_check"), s.get("update_check_days", 7))]
    due.sort(key=lambda r: r.get("last_update_check") or "")
    found = []
    for rec in due[: s.get("update_checks_per_run", 8)]:
        rel = src.github_latest_release(gh, rec["name"])
        rec["last_update_check"] = now_iso()
        if not rel or not rel.get("tag"):
            continue
        old = rec.get("version")
        rec["version"] = rel["tag"]
        if old and old != rel["tag"] and meaningful_update(old, rel["tag"], rel):
            body = ev.sanitize(rel.get("body"), 600)
            summary = (rel.get("name") or rel["tag"]) + (": " + body if body else "")
            found.append({"rid": rec["id"], "name": rec["name"], "version_from": old, "version_to": rel["tag"],
                          "summary": summary, "url": rel.get("url")})
            print(f"    🔄 {rec['name']}: {old} → {rel['tag']}")
    pending = store.meta.setdefault("pending_updates", [])
    known = {(u["rid"], u["version_to"]) for u in pending}
    pending += [u for u in found if (u["rid"], u["version_to"]) not in known]
    return found


# ----------------------------------------------------------------------------------------------- batching & delivery

def choose_batch(cfg, store):
    """The pool's best resources: one entry per resource, a few per category section, P0 first."""
    s = cfg["settings"]
    for rec in store.by_status("pool"):
        if older_than(rec.get("evaluated_at"), s.get("pool_days", 10)):
            rec["status"] = "expired"
    pool = sorted(store.by_status("pool"), key=lambda r: (msg.PRIORITY_RANK.get(r.get("priority"), 9), -r.get("score", 0)))
    picked, per_group = [], {}
    for rec in pool:
        group = render.GROUP_OF[rec["evaluation"]["primary"]]
        if per_group.get(group, 0) >= s.get("max_per_section", 3):
            continue
        per_group[group] = per_group.get(group, 0) + 1
        picked.append(rec)
        if len(picked) >= s.get("max_per_batch", 8):
            break
    best = picked[0]["priority"] if picked else None
    if picked and msg.PRIORITY_RANK[best] > msg.PRIORITY_RANK[s.get("send_only_if_best", "P2")]:
        print(f"  {len(picked)} resource(s) in the pool, best only {best}: no batch this time")
        picked = []
    return picked


def prepare(cfg, config, store, picked, updates, period_start):
    batch = []
    for rec in picked:
        c = dict(rec["snapshot"])
        c["rid"] = rec["id"]
        batch.append((c, rec["evaluation"], src.readme(c, 4000)))
    explanations, best_i, best, model = ex.explain(config, batch)
    items = [{"candidate": c, "evaluation": e, "explanation": explanations[c["rid"]]} for c, e, _ in batch]
    messages = render.render(items, updates, best_i, best, period_start, now_utc(), model)
    return items, messages


def apply_sent(store, n):
    """After Telegram confirmed a batch: every resource in it becomes 'delivered' with the message ids."""
    if n.get("applied") or n["status"] != "SENT":
        return
    ids = [n["sent_parts"][k]["message_id"] for k in sorted(n["sent_parts"], key=int)]
    ledger = led.Ledger()
    for rid in n.get("resource_ids", []):
        rec = store.resources.get(rid)
        if rec:
            rec["status"] = "delivered"
            rec["delivery"] = {"status": "SENT", "message_ids": ids, "sent_at": n["sent_at"]}
            rec["last_notified"] = n["sent_at"]
            rec.pop("evaluation", None)  # the history keeps name, URL, categories, score, licence and delivery
            ledger.record([led.url_key(rec["url"])], rec["name"], "discovery")
    for u in n.get("updates", []):
        rec = store.resources.get(u["rid"])
        if rec:
            rec["last_notified"] = n["sent_at"]
            rec.setdefault("updates_sent", []).append(u["version_to"])
    store.meta["pending_updates"] = [u for u in store.meta.get("pending_updates", [])
                                     if (u["rid"], u["version_to"]) not in {(x["rid"], x["version_to"]) for x in n.get("updates", [])}]
    n["applied"] = True
    ledger.save()


def deliver_pending(cfg, store):
    """Send what is waiting in the outbox. Returns True when something is still undelivered."""
    settings = {"max_delivery_attempts": cfg["settings"].get("max_delivery_attempts", 6), "receipts_for": []}
    for n in store.pending():
        n.setdefault("priority", "P1")
        notify.send_one(store, n, settings, store.save)
        if n["status"] == "SENT":
            apply_sent(store, n)
        elif n["status"] == "FAILED":
            for rid in n.get("resource_ids", []):  # give up on this batch: its resources may be chosen again
                if rid in store.resources:
                    store.resources[rid]["status"] = "pool"
                    store.resources[rid]["delivery"] = {"status": "FAILED", "message_ids": [], "sent_at": None}
        store.save()
    return bool(store.pending())


def run(dry_run=False, cfg=None, config=None, store=None):
    cfg = cfg or dconfig.load()
    errors = dconfig.validate(cfg)
    if errors:
        print("discovery.yaml has problems:\n  " + "\n  ".join(errors))
        return 2
    config = config or load_yaml("config.yaml")
    store = store or DiscoveryStore()
    s = cfg["settings"]
    period_start = parse_date(store.meta.get("last_batch_at")) or now_utc() - timedelta(hours=3)

    if not dry_run and store.pending():
        print("Delivering a batch from an earlier run first…")
        if deliver_pending(cfg, store):
            print("Telegram hasn't confirmed the earlier batch yet: no new batch this run (it is retried next time).")
            store.save()
            return 1

    print("Searching discovery sources…")
    gh = src.GitHub(s.get("max_github_requests", 120))
    candidates, failed = collect(cfg, store, gh)
    candidates = dedupe(candidates)
    passed, new = evaluate_new(cfg, store, gh, candidates)
    print(f"{len(candidates)} candidates, {new} new, {passed} passed evaluation; GitHub requests used: {gh.used}")
    try:
        check_updates(cfg, store, gh)
    except Exception as e:  # noqa: BLE001
        print(f"  update check skipped: {str(e)[:120]}")
    updates = list(store.meta.get("pending_updates", []))[:5]
    picked = choose_batch(cfg, store)
    if not picked and not updates:
        print("Nothing new that is good enough: no Telegram message this time.")
        if not dry_run:
            store.save()
        return 0
    items, messages = prepare(cfg, config, store, picked, updates, period_start)
    for m in messages:
        print(msg.strip_html(m) + "\n")
    if dry_run:
        print("(dry run: nothing sent or saved)")
        return 0

    nid = f"discovery|{now_iso()}"
    store.outbox[nid] = {"id": nid, "release_id": nid, "kind": "discovery", "category": "DISCOVERY",
                         "verification": "", "model_name": "AI skills & open-source discoveries", "parts": messages,
                         "sent_parts": {}, "status": "PENDING", "attempts": 0, "created": now_iso(),
                         "priority": picked[0]["priority"] if picked else "P2", "sent_at": None, "last_error": None,
                         "log": [], "receipt": {"status": "NOT_NEEDED"},
                         "resource_ids": [r["id"] for r in picked], "updates": updates}
    for rec in picked:
        rec["status"] = "queued"
        rec["delivery"] = {"status": "PENDING", "message_ids": [], "sent_at": None}
    store.meta["last_batch_at"] = now_iso()
    store.save()
    still = deliver_pending(cfg, store)
    if not still:
        LAST_DIGEST.parent.mkdir(exist_ok=True)
        LAST_DIGEST.write_text(json.dumps(messages, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        botpush.push("last_discovery", messages)
    store.save()
    return 1 if still else 0
