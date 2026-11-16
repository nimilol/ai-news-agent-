"""The release monitor run (`python -m agent releases`) and its source check (`python -m agent check-releases`).

    fetch every official source (and the early-signal listings) in parallel
      → a source read for the first time is learned silently (no alerts for what is already there)
      → compare with memory: new posts, changelog entries, catalog listings, status/spec changes, repositories
      → classify (classify.py) → read and compare confirmed launches (analyze.py)
      → save the release history → queue Telegram alerts → deliver with confirmation (notify.py)
"""

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from .. import botpush
from ..common import ROOT, load_yaml, now_utc, parse_date
from . import classify as cls
from . import config as rconfig
from . import identity, notify, render
from .analyze import analyze, summarize_update
from .fetchers import Skipped, fetch_early, fetch_source
from .store import Store, now_iso

MAX_POST_AGE_DAYS = 14       # an "unseen" post older than this is a page reshuffle, not news
MAX_ENTRY_AGE_DAYS = 21      # same for changelog entries and new repositories
FOLLOWUP_DAYS = 14           # a new official post about a release this recent is sent as an update to it


def _specs_hash(entry):
    return hashlib.sha1(json.dumps({"s": entry.get("specs") or {}, "st": entry.get("status")},
                                   sort_keys=True, default=str).encode()).hexdigest()[:12]


def _too_old(value, days):
    if not value:
        return False
    if len(value) == 7:  # "YYYY-MM": only the month is known
        value = value + "-28"
    d = parse_date(value)
    if d and d.tzinfo is None:
        d = d.replace(tzinfo=now_utc().tzinfo)
    return bool(d) and now_utc() - d > timedelta(days=days)


def run_fetches(cfg):
    """{source name: result | Exception | Skipped} for every company source and early signal."""
    jobs = [(s, cfg["companies"][s["company"]]) for c in cfg["companies"].values() for s in c["sources"]]
    jobs += [(dict(s, role="early"), None) for s in cfg["early_signals"]]

    def one(job):
        source, company = job
        try:
            if source["role"] == "early":
                return source["name"], {"kind": "early", "entries": fetch_early(source, cfg["companies"])}
            return source["name"], fetch_source(source, company)
        except Exception as e:  # noqa: BLE001 - one broken source never stops the run
            return source["name"], e

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(one, jobs)), {s["name"]: (s, c) for s, c in jobs}


def _obs(company, source, event, official=True, **kw):
    o = {"company": company, "source": source["name"], "role": source["role"], "official": official,
         "event": event, "model_ids": [], "mentions": [], "title": "", "url": None, "text": "", "date": None,
         "tags": [], "status": None, "specs": {}, "fingerprints": [], "uid": ""}
    o.update(kw)
    return o


def observe(source, company_cfg, result, store, first_run):
    """Compare a source's entries with what was seen before. Updates memory (and the model registry) and returns
    the new observations. On a source's first run everything is learned and nothing is reported."""
    name, role = source["name"], source["role"]
    company = source.get("company")
    entries = result["entries"]
    out = []
    if role == "catalog":
        mem = store.source(name).setdefault("models", {})
        for e in entries:
            h, old = _specs_hash(e), mem.get(e["id"])
            mem[e["id"]] = {"status": e.get("status"), "h": h}
            store.learn_model(company, e, name)
            if first_run or (old and old["h"] == h):
                continue
            base = dict(model_ids=[e["id"]], url=e.get("url") or source.get("url"), specs=e.get("specs") or {},
                        status=e.get("status"), text=(e.get("specs") or {}).get("description", ""),
                        fingerprints=[f"{identity.url_fingerprint(source.get('url'))}#{e['id']}"],
                        uid=f"{name}:{e['id']}")
            if old is None:
                out.append(_obs(company, source, "listed", title=f"{name} lists {e.get('name') or e['id']}",
                                tags=["preview"] if e.get("status") == "preview" else [], **base))
            elif old.get("status") != e.get("status"):
                tags = ["deprecation"] if e.get("status") in ("deprecated", "retired", "restricted") else ["availability"]
                out.append(_obs(company, source, "status_change", tags=tags,
                                title=f"{e.get('name') or e['id']}: {old.get('status')} → {e.get('status')} ({name})",
                                **base))
            else:
                out.append(_obs(company, source, "spec_change", tags=["pricing"],
                                title=f"{e.get('name') or e['id']}: details changed on {name}", **base))
        return out

    seen = store.seen(name)
    new_uids = []
    for e in entries:
        uid = e.get("uid") or e.get("id")
        if uid in seen:
            continue
        new_uids.append(uid)
        if first_run:
            continue
        if role == "changelog":
            if _too_old(e.get("date"), MAX_ENTRY_AGE_DAYS) or not (e.get("model_ids")):
                continue
            fps = [identity.content_fingerprint(e["title"], e["text"][:300])]
            if e.get("url") and "#" in e["url"]:
                fps.append(identity.url_fingerprint(e["url"]) + "#" + e["url"].split("#", 1)[1])
            out.append(_obs(company, source, "changelog", model_ids=e["model_ids"], mentions=e.get("mentions", []),
                            title=e["title"], text=e["text"], url=e.get("url") or source.get("url"),
                            date=e.get("date"), tags=e.get("tags", []), fingerprints=fps, uid=f"{name}:{uid}"))
        elif role == "announcement":
            if _too_old(e.get("published"), MAX_POST_AGE_DAYS):
                continue
            patterns = company_cfg["_patterns"]
            ids = identity.extract_models(e["title"], patterns) or identity.extract_models(e["summary"], patterns)[:1]
            unidentified = not ids and bool(cls.RELEASE_TITLE.search(e["title"])) and \
                bool(re.search(r"\bmodels?\b", e["title"], re.I))
            if not ids and not unidentified:
                continue
            # A feed item counts as official only when it links to the company's own site.
            official = rconfig.is_official_url(e["url"], company_cfg)
            out.append(_obs(company, source, "announcement", official=official, model_ids=ids, title=e["title"],
                            text=e["summary"],
                            url=e["url"], date=(e.get("published") or "")[:10] or None, unidentified=unidentified,
                            fingerprints=[identity.url_fingerprint(e["url"]), identity.content_fingerprint(e["title"])],
                            uid=f"{name}:{uid}"))
        elif role == "repo":
            if _too_old((e.get("specs") or {}).get("created"), MAX_ENTRY_AGE_DAYS):
                continue
            out.append(_obs(company, source, "repo_created", model_ids=e.get("model_ids") or [e["name"]],
                            title=f"New repository {e['id']}", text=(e.get("specs") or {}).get("description", ""),
                            url=e.get("url"), date=(e.get("specs") or {}).get("created") or None,
                            fingerprints=[identity.url_fingerprint(e.get("url"))], uid=f"{name}:{e['id']}"))
        elif role == "early":
            out.append(_obs(e["company"], source, "early_listing", official=False, model_ids=e["model_ids"],
                            title=e["title"], text=e["text"], url=e["url"],
                            fingerprints=[identity.url_fingerprint(e["url"])], uid=f"{name}:{uid}"))
    store.remember(name, new_uids)
    return out


# ----------------------------------------------------------------------------------------------- history & alerts

def _primary_url(facts):
    for kind in ("announcement", "changelog"):
        t = next((t for t in facts["texts"] if t["kind"] == kind and t.get("url")), None)
        if t:
            return t["url"]
    o = next((o for o in facts["official"] if o.get("url")), None)
    return o["url"] if o else None


def record_launch(store, facts):
    """Create (or upgrade an earlier unconfirmed) history record for a new model launch."""
    company, bases = facts["company"], facts["bases"]
    existing = next((store.release_for(company, b) for b in bases if store.release_for(company, b)), None)
    fields = dict(
        model_name=facts["model_name"], model_ids=facts["model_ids"],
        previous_model={k: facts["previous"][k] for k in ("name", "id", "method")} if facts["previous"] else None,
        classification=facts["category"], verification=facts["verification"],
        announcement_date=facts["announcement_date"], availability_date=facts["availability_date"],
        availability_status=facts["availability_status"], source_url=_primary_url(facts),
        official_sources=[{"source": o["source"], "url": o.get("url"), "kind": o["event"]} for o in facts["official"]],
        fingerprints=sorted({fp for o in facts["official"] for fp in o.get("fingerprints", [])}),
        comparison_summary=facts["comparison_summary"],
        details={"whats_new": facts["whats_new"], "improved": facts["improved"], "regressed": facts["regressed"],
                 "unchanged": facts["unchanged"], "benchmarks": facts["ai"].get("benchmarks", []),
                 "claims": facts["ai"].get("claims", []), "reasons": facts["reasons"],
                 "ai_model": facts["ai_model"], "specs": facts["specs"], "previous_specs": facts["previous_specs"],
                 "comparison": facts["comparison"], "explain": facts.get("explain") or {},
                 "api_access": facts["api_access"], "flagship": facts.get("flagship", False),
                 "priority": facts.get("priority")})
    if existing:
        was = existing.get("verification")
        existing.update(fields)
        for b in bases:
            store.index[f"{company}:{b}"] = existing["id"]
        store.log_event(existing, "confirmed" if was == cls.UNCONFIRMED else "updated",
                        f"{was} → {facts['verification']}")
        return existing
    rec = store.new_release(company, bases, **fields)
    store.log_event(rec, "detected", "; ".join(facts["reasons"][:2]))
    return rec


def followup_note(existing, result):
    """New, meaningful information about a recently announced release, or None."""
    if not existing or existing.get("verification") not in (cls.CONFIRMED, cls.UPDATE):
        return None
    detected = parse_date(existing.get("first_detected"))
    if not detected or now_utc() - detected > timedelta(days=FOLLOWUP_DAYS):
        return None
    known = set(existing.get("fingerprints", []))
    new = [o for o in result["official"] if not set(o.get("fingerprints", [])) & known]
    if not new:
        return None
    has_announcement = any(s.get("kind") == "announcement" for s in existing.get("official_sources", []))
    post = next((o for o in new if o["event"] == "announcement"), None)
    if post and not has_announcement:
        return f"The official announcement is now published: {post['title']}", new
    if not existing.get("availability_date"):
        avail = next((o for o in new if "availability" in o.get("tags", []) and o.get("date")), None)
        if avail:
            return f"Availability confirmed ({avail['date']}): {avail['title']}", new
    return None


def run(dry_run=False, bootstrap=False, cfg=None, config=None, store=None):
    cfg = cfg or rconfig.load()
    errors = rconfig.validate(cfg)
    if errors:
        print("releases.yaml has problems:\n  " + "\n  ".join(errors))
        return 2
    config = config or load_yaml("config.yaml")
    store = store or Store()
    settings, companies = cfg["settings"], cfg["companies"]
    print(f"Checking official model sources for {', '.join(c['label'] for c in companies.values())}…")
    results, by_name = run_fetches(cfg)

    # 1. Health, and first runs (learned silently).
    failing, learned = [], set()
    for name, res in results.items():
        source, company_cfg = by_name[name]
        if isinstance(res, Skipped):
            print(f"  - {name}: skipped ({res})")
            continue
        if isinstance(res, Exception):
            store.record_result(name, False)
            failing.append((name, source, res))
            print(f"  ✗ {name}: {str(res)[:150]}")
            continue
        store.record_result(name, True)
        store.health.pop(name, None)
        if bootstrap or store.is_new_source(name):
            observe(source, company_cfg, res, store, first_run=True)
            store.mark_initialized(name)
            learned.add(name)
            print(f"  ✓ {name}: learned {len(res['entries'])} entries (first run, nothing reported)")
    store.known_before = {k: store.known_bases(k, c) for k, c in companies.items()}
    store.known_exact_before = {k: store.known_exact(k, c) for k, c in companies.items()}

    # 2. What's new since last time.
    observations = []
    for name, res in results.items():
        source, company_cfg = by_name[name]
        if isinstance(res, Exception) or name in learned:
            continue
        new = observe(source, company_cfg, res, store, first_run=False)
        if new:
            print(f"  ✓ {name}: {len(new)} new")
        observations.extend(new)

    # 3. Classify, analyse, record, queue.
    queued = []
    llm_budget = {"left": int(settings.get("max_llm_calls", 4))}
    classified = cls.classify(observations, store, companies)
    launches = cls.merge_launches(classified)
    in_launch = {id(r) for group in launches for r in group}
    for group in launches:
        company_cfg = companies[group[0]["company"]]
        print(f"→ {company_cfg['label']}: possible new model {', '.join(r['base'] for r in group)}")
        facts = analyze(group, store, company_cfg, config, settings, llm_budget)
        rec = record_launch(store, facts)
        rec["priority"] = facts["priority"]
        print(f"   {cls.VERIFICATION_LABEL[facts['verification']]}: {facts['model_name']} "
              f"(previous: {facts['previous']['name'] if facts['previous'] else 'not established'})")
        if facts["verification"] == cls.CONFIRMED and settings["notify"].get(cls.A):
            parts, kind = render.render_release(facts, companies), "release"
        elif facts["verification"] == cls.REVIEW and settings["notify"].get("REQUIRES_MANUAL_REVIEW"):
            parts, kind = render.render_review(facts, companies), "review"
        else:
            continue
        priority = facts["priority"] if kind == "release" else "P1"
        queued.append(store.enqueue(rec["id"], kind, cls.A, facts["verification"], facts["model_name"], parts, priority))
        if not dry_run and facts["verification"] == cls.CONFIRMED:
            archive(facts, parts)

    for r in classified:
        if id(r) in in_launch:
            continue
        company_cfg = companies[r["company"]]
        cat, ver = r["category"], r["verification"]
        existing = r.get("existing")
        note = followup_note(existing, r) if r["official"] else None
        if note:
            text, new_obs = note
            existing["fingerprints"] = sorted(set(existing["fingerprints"]) |
                                              {fp for o in new_obs for fp in o["fingerprints"]})
            existing["official_sources"] += [{"source": o["source"], "url": o.get("url"), "kind": o["event"]}
                                             for o in new_obs]
            if any(o["event"] == "announcement" for o in new_obs):
                existing["source_url"] = next(o["url"] for o in new_obs if o["event"] == "announcement")
            store.log_event(existing, "followup", text)
            print(f"→ update to {existing['model_name']}: {text}")
            parts = render.render_followup(existing, text, [{"name": o["source"], "url": o.get("url")} for o in new_obs],
                                           companies)
            queued.append(store.enqueue(existing["id"], "followup", existing["classification"], cls.UPDATE,
                                        existing["model_name"], parts, "P1"))
            continue
        if ver != cls.DUPLICATE and cat in (cls.B, cls.C) and existing and \
                existing.get("verification") in (cls.CONFIRMED, cls.UPDATE) and r["official"] and \
                all(o.get("date") and o["date"][:10] <= existing["first_detected"][:10] for o in r["official"]):
            ver = cls.DUPLICATE  # a reworded entry about the launch itself, not a new development
        if ver == cls.DUPLICATE:
            if existing:
                existing["duplicates"] = existing.get("duplicates", 0) + 1
                store.log_event(existing, "duplicate", "; ".join(o["source"] for o in r["official"] + r["unofficial"]))
            print(f"  = duplicate of an earlier release: {r['company']}:{r['base']}")
            continue
        facts = summarize_update(r, store, company_cfg)
        print(f"  {cls.CATEGORY_LABEL[cat]} · {cls.VERIFICATION_LABEL[ver]}: {facts['model_name']} — {facts['title'][:90]}")
        if cat in (cls.D, cls.E):
            store.log_change({"company": r["company"], "model": facts["model_name"], "category": cat,
                              "title": facts["title"][:200], "sources": [s["name"] for s in facts["sources"]]})
            if not settings["notify"].get(cat):
                continue
            rid = f"{r['company']}:{r['base']}@{cat}:{identity.content_fingerprint(facts['title'])}"
        elif cat == cls.F:
            if existing:  # an earlier signal for the same model: recorded once, alerted once
                existing["fingerprints"] = sorted(set(existing.get("fingerprints", [])) |
                                                  {fp for o in r["unofficial"] for fp in o["fingerprints"]})
                store.log_event(existing, "signal", "; ".join(o["source"] for o in r["unofficial"]))
                continue
            rid = None
        elif cat == cls.B:
            exact = sorted({identity.exact_key(m, company_cfg) for m in facts["model_ids"]})
            rid = f"{r['company']}:{r['base']}@{cat}:{'+'.join(exact)}"
        else:
            rid = f"{r['company']}:{r['base']}@{cat}:{(facts['date'] or now_iso()[:10])[:7]}"
        if rid and rid in store.history:
            rec = store.history[rid]
            store.log_event(rec, "duplicate", "; ".join(s["name"] for s in facts["sources"]))
            rec["duplicates"] = rec.get("duplicates", 0) + 1
            continue
        rec = store.new_release(r["company"], [r["base"]] if cat == cls.F else [], rid=rid)
        rec.update(model_name=facts["model_name"], model_ids=facts["model_ids"], classification=cat,
                   priority=facts["priority"],
                   verification=ver, announcement_date=facts["date"],
                   source_url=next((s["url"] for s in facts["sources"] if s.get("url")), None),
                   official_sources=[{"source": s["name"], "url": s["url"]} for s in facts["sources"]],
                   fingerprints=sorted({fp for o in r["official"] + r["unofficial"] for fp in o["fingerprints"]}),
                   comparison_summary=facts["title"][:200], details={"reasons": r["reasons"], "text": facts["text"]})
        store.log_event(rec, "detected", "; ".join(r["reasons"][:2]))
        if not settings["notify"].get(cat):
            continue
        queued.append(store.enqueue(rec["id"], "update", cat, ver, facts["model_name"],
                                    render.render_update(facts, companies), facts["priority"]))

    # 4. Health alerts for official sources that keep failing.
    threshold = int(settings.get("failing_alert_after", 3))
    for name, source, err in failing:
        if store.failures.get(name, 0) >= threshold and not store.health.get(name, {}).get("alerted") \
                and source["role"] != "early":
            label = companies[source["company"]]["label"]
            store.enqueue(f"health:{name}", "health", "HEALTH", "", name,
                          render.render_health(name, label, store.failures[name], str(err)), "P1")
            store.health[name] = {"alerted": True, "since": now_iso()}

    print(f"{len(observations)} new observations, {len(classified)} classified, {len(queued)} alert(s) queued.")
    if dry_run:
        for n in store.pending():
            print("\n" + render.plain(n["parts"]))
        print("\n(dry run: nothing sent, state not saved)")
        return 0

    enrich_specs(store, companies, int(settings.get("spec_pages_per_run", 8)))
    store.save()
    summary = notify.process_outbox(store, settings, store.save, int(settings.get("max_notifications_per_run", 8)))
    store.save()
    push_to_bot(store, cfg)
    print(f"Delivery: {summary}")
    return 1 if summary.get("FAILED") else 0


def enrich_specs(store, companies, budget=8):
    """Fill in specs (context, output limit, prices) for catalog models that only have a name, a few detail pages
    per run, so /compare and predecessor comparisons have numbers for every lab. Each model is tried at most once
    a week."""
    from .fetchers import fetch_detail

    week_ago = (now_utc() - timedelta(days=7)).isoformat()
    for key, c in companies.items():
        src = next((s for s in c["sources"] if s.get("role") == "catalog" and s.get("detail_url")), None)
        if not src:
            continue
        for mid, rec in store.models(key).items():
            if budget <= 0:
                return
            specs = rec.get("specs") or {}
            if specs.get("context_tokens") or rec.get("status") == "deprecated" or \
                    (rec.get("specs_checked") or "") > week_ago or src["name"] not in rec.get("sources", []):
                continue
            budget -= 1
            rec["specs_checked"] = now_iso()
            got = fetch_detail(src, mid)
            if got:
                rec["specs"] = {**specs, **got}


def push_to_bot(store, cfg):
    """Keep the instant bot's /releases, /models, /compare, /sources and /health answers current."""
    if not botpush.bot_url():
        return
    from .. import health
    from . import catalog

    companies = cfg["companies"]
    botpush.push("last_releases", render.render_recent(store.recent_releases(), companies))
    botpush.push("last_models", render.render_models(store.recent_releases(30), companies))
    botpush.push("models_index", catalog.compact_index(catalog.model_index(store, companies)))
    botpush.push("sources", render.render_sources(cfg, store.failures))
    botpush.push("health", health.render())


def archive(facts, parts):
    """A Markdown copy of every confirmed release alert in releases/, like digests/ for the briefs."""
    day = facts["announcement_date"] if facts["announcement_date"] and len(facts["announcement_date"]) == 10 \
        else now_utc().date().isoformat()
    path = ROOT / "releases" / f"{day}_{facts['company']}_{'+'.join(facts['bases'])[:80]}.md"
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        path.write_text(f"# {facts['model_name']}\n\n{render.plain(parts)}\n", encoding="utf-8")


def check(cfg=None):
    """Validate releases.yaml and read every source once (nothing saved, nothing sent)."""
    cfg = cfg or rconfig.load()
    errors = rconfig.validate(cfg)
    print("releases.yaml: " + ("valid" if not errors else "PROBLEMS:\n  " + "\n  ".join(errors)))
    results, by_name = run_fetches(cfg)
    ok = 0
    for name, res in results.items():
        source, company_cfg = by_name[name]
        who = company_cfg["label"] if company_cfg else "early signal"
        if isinstance(res, Skipped):
            print(f"-  skip  {who:<12} {name:<42} {res}")
        elif isinstance(res, Exception):
            print(f"✗  FAIL  {who:<12} {name:<42} {str(res)[:100]}")
        else:
            ok += 1
            entries = res["entries"]
            sample = ", ".join((e.get("id") or e.get("title") or "")[:30] for e in entries[:3])
            print(f"✓  ok    {who:<12} {name:<42} {len(entries):>4} entries · {sample}")
    print(f"\n{ok}/{len(results)} release sources working.")
    return 0 if not errors else 2
