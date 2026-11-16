"""Model Access Intelligence runs.

    refresh()   every 6 hours, as a step of the existing news digest workflow (no new schedule): re-check the
                provider rules and platform pages, load the catalogs, merge into the database, queue and deliver
                free-access alerts, push the /modelaccess replies to the always-on bot
    weekly()    Saturdays, as a step of the existing weekly model report workflow: the Weekly Model Access Report
    answer()    the hourly Python bot's /modelaccess reply (from the database; builds it first if it never ran)
"""

import json

from .. import botpush
from .. import messages as msg
from ..common import now_utc
from . import alerts as alert_mod
from . import config as mconfig
from . import respond, sources
from .analyze import best_free
from .db import AccessDB, hours_since, now_iso, weekly_path
from .normalize import Builder, norm
from .render import checked_line, e, finish, premium_extra, change_text


def verify_rules(db, cfg):
    """Re-check each free-tier provider's published terms (e.g. OpenRouter's :free limits). Returns {pid: True}."""
    ok = {}
    for pid, rule in cfg["providers"]["free_tier"].items():
        v = (rule or {}).get("verify")
        if not v:
            continue
        try:
            passed = sources.check_page(v["url"], v["patterns"])
            db.source_result(f"verify:{pid}", True)
        except Exception as ex:  # noqa: BLE001 - keep the previous result for a page that didn't load
            db.source_result(f"verify:{pid}", False, ex)
            passed = bool((db.meta.get("verified_rules") or {}).get(pid))
        db.meta.setdefault("verified_rules", {})[pid] = now_iso()[:10] if passed else None
        ok[pid] = passed
        print(f"  free-tier terms {pid}: {'confirmed' if passed else 'NOT confirmed'}")
    return ok


def check_platforms(db, cfg):
    """Platform / app / credits entries: confirmed only while their source page still says what they claim.
    Returns status-change events for alerts."""
    events = []
    for p in cfg["platforms"]:
        st = db.platforms.setdefault(p["id"], {})
        old = st.get("shown_status")
        v = p.get("verify")
        if v:
            try:
                found = sources.check_page(v["url"], v["patterns"])
                st["check"] = "ok" if found else "claim_not_found"
                st["checked"] = now_iso()
                if found:
                    st["verified"] = now_iso()[:10]
                st["shown_status"] = p.get("status") if found else "UNVERIFIED"
            except Exception as ex:  # noqa: BLE001
                st["check"] = f"error: {str(ex)[:120]}"
                st.setdefault("shown_status", "UNVERIFIED")
        elif p.get("verified"):
            st["verified"] = str(p["verified"])
            st["shown_status"] = p.get("status")
        else:
            st["shown_status"] = "UNVERIFIED"
        if st.get("shown_status") != old and old is not None and st["shown_status"] != "UNVERIFIED":
            events.append({"platform": p, "old": old, "new": st["shown_status"], "verified": st.get("verified")})
    return events


def enrich_routes(db, limit):
    """OpenRouter's per-provider breakdown for a few models per run, oldest first (rate-limit friendly)."""
    rows = [o for o in db.offers.values() if o.get("provider") == "openrouter" and o.get("active", True)
            and o.get("access") == "paid_api" and o.get("listing")]
    rows.sort(key=lambda o: (o.get("routes_checked") or "", -(db.models.get(o["model"], {}).get("providers_listed") or 0)))
    done = 0
    for o in rows[:limit]:
        try:
            eps = sources.openrouter_endpoints(o["listing"])
        except Exception as ex:  # noqa: BLE001
            print(f"  OpenRouter endpoints for {o['listing']}: {str(ex)[:100]}")
            continue
        routes = []
        for ep in eps:
            pr = ep.get("pricing") or {}
            try:
                routes.append({"name": ep.get("provider_name") or ep.get("name"), "in": round(float(pr["prompt"]) * 1e6, 6),
                               "out": round(float(pr["completion"]) * 1e6, 6), "uptime": ep.get("uptime_last_1d")})
            except (KeyError, TypeError, ValueError):
                continue
        o["routes"] = sorted(routes, key=lambda r: (3 * r["in"] + r["out"]))[:6]
        o["routes_checked"] = now_iso()
        done += 1
    return done


def collect_leads(db, cfg):
    try:
        posts = sources.leads(cfg.get("leads") or {})
        db.source_result("reddit_leads", True)
    except Exception as ex:  # noqa: BLE001
        db.source_result("reddit_leads", False, ex)
        return 0
    names = {}
    for mid, m in db.models.items():
        for k in {norm(m.get("name")), norm(mid.split("/", 1)[1])}:
            if k and len(k) >= 6 and "-" in k:
                names[k] = mid
    new = 0
    by_model = db.offers_by_model()
    for p in posts:
        title = p.get("title") or ""
        if mconfig.excluded(title, cfg) or not p.get("url"):
            continue
        lid = "rd:" + norm(p["url"])[-60:]
        if lid in db.leads:
            continue
        t = norm(title + " " + (p.get("text") or "")[:400])
        models = sorted({mid for k, mid in names.items() if f"-{k}-" in f"-{t}-"})[:4]
        note = None
        for mid in models:
            free = best_free(by_model.get(mid, []), hosted_only=True)
            if free:
                note = f"a confirmed free offer exists: {free.get('provider_name')}"
                break
        db.leads[lid] = {"id": lid, "title": title[:200], "url": p["url"], "source": f"r/{p.get('sub') or 'reddit'}",
                         "models": models, "status": "UNVERIFIED", "confirmed_note": note, "first_seen": now_iso()}
        new += 1
    return new


def build_catalog(db, cfg, verified):
    cat = cfg["catalogs"]
    loaded, md, orr, gem = set(), None, None, None
    for name, fn, url in (("models_dev", sources.models_dev, cat.get("models_dev")),
                          ("gemini", sources.gemini_pricing, cat.get("gemini_pricing")),
                          ("openrouter", sources.openrouter, cat.get("openrouter"))):
        if not url:
            continue
        try:
            data = fn(url)
            db.source_result(name, True)
            loaded.add(name)
            md, gem, orr = (data, gem, orr) if name == "models_dev" else (md, data, orr) if name == "gemini" else (md, gem, data)
            print(f"  {name}: loaded")
        except Exception as ex:  # noqa: BLE001 - the last good data for this source is kept
            db.source_result(name, False, ex)
            print(f"  {name}: FAILED ({str(ex)[:150]}); keeping the last good data")
    b = Builder(cfg, verified)
    if md:
        b.add_models_dev(md, gem)
    if orr:
        b.add_openrouter(orr)
    if gem:
        b.add_gemini_free(gem, "https://ai.google.dev/gemini-api/docs/pricing")
    models, offers = b.finish()
    return models, offers, loaded


def push_bot(db, bundle):
    """Send the /modelaccess replies to the Cloudflare bot; only pieces that changed since the last push."""
    if not botpush.bot_url():
        return 0
    hashes = db.meta.setdefault("bot_hashes", {})
    pushed = 0
    for key, value in respond.bot_payloads(bundle).items():
        h = respond.payload_hash(value)
        if hashes.get(key) == h:
            continue
        if botpush.push(key, value):
            hashes[key] = h
            pushed += 1
    return pushed


def refresh(dry_run=False, deliver_alerts=True, quick=False):
    """One refresh. Returns 0 when the database was updated (even if some sources failed), 1 when nothing loaded."""
    cfg = mconfig.load()
    s = cfg["settings"]
    db = AccessDB()
    print("Model Access Intelligence refresh")
    if not quick:
        verify_rules(db, cfg)  # a quick refresh reuses the last check
    verified = {pid: bool(v) for pid, v in (db.meta.get("verified_rules") or {}).items()}
    models, offers, loaded = build_catalog(db, cfg, verified)
    if not loaded:
        print("No catalog loaded; the database is unchanged.")
        if not dry_run:
            db.save(s["history_keep"], s["leads_keep_days"])
        return 1
    baseline = not db.meta.get("bootstrapped")
    events = db.apply(models, offers, loaded)
    print(f"  {len(db.models)} models, {sum(1 for o in db.offers.values() if o.get('active', True))} active offers; "
          f"{len(events)} changes" + (" (baseline run: changes are recorded from the next run)" if baseline else ""))
    platform_events = [] if quick else check_platforms(db, cfg)
    if not quick:
        print(f"  OpenRouter provider breakdowns refreshed: {enrich_routes(db, int(s['openrouter_endpoint_lookups']))}")
        print(f"  new community leads (UNVERIFIED): {collect_leads(db, cfg)}")
    candidates = [] if baseline else alert_mod.build(db, events, cfg, platform_events)
    fresh = alert_mod.fresh(db, candidates, cfg)
    print(f"  alerts: {len(candidates)} candidates, {len(fresh)} new")
    if dry_run:
        for part in alert_mod.render(fresh) if fresh else []:
            print(msg.strip_html(part) + "\n")
        print("(dry run: nothing sent or saved)")
        return 0
    if fresh and deliver_alerts:
        alert_mod.queue(db, fresh)
    db.save(s["history_keep"], s["leads_keep_days"])
    if deliver_alerts:
        deliver_pending(db)
    bundle, _, _ = respond.build(db, cfg)
    if not quick:
        print(f"  bot pieces updated: {push_bot(db, bundle)}")
    db.save(s["history_keep"], s["leads_keep_days"])
    return 0


def deliver_pending(db):
    from ..releases import notify

    for n in db.pending():
        notify.send_one(db, n, {"max_delivery_attempts": 6}, db.save)
    alert_mod.mark_sent(db)
    db.save()
    return [n["id"] for n in db.pending()]


def answer(args):
    """The /modelaccess reply for the hourly Python bot."""
    cfg = mconfig.load()
    db = AccessDB()
    if not db.models:
        try:
            refresh(deliver_alerts=False, quick=True)  # first use: build the database once, then answer
            db = AccessDB()
        except Exception as ex:  # noqa: BLE001
            print(f"  model access refresh failed: {str(ex)[:150]}")
    if not db.models:
        return respond.render.render_empty()
    bundle, _, _ = respond.build(db, cfg)
    return respond.route(bundle, args)


# ---------------------------------------------------------------------- weekly report
def weekly_report(db, cfg):
    from . import analyze

    s = cfg["settings"]
    since = db.meta.get("weekly_last")
    hours = min(24 * 7.5, hours_since(since) or 24 * 7) if since else 24 * 7
    events = db.events_since(hours)
    views = analyze.all_views(db, s)
    board = analyze.leaderboard(views, s)
    opps = {}

    def add(mid, score, why):
        if mid in views and (mid not in opps or opps[mid][0] < score):
            opps[mid] = (score, why)

    new_free, prices, ended = [], [], []
    for ev in events:
        o = db.offers.get(ev.get("offer") or "") or {}
        mid = ev.get("model")
        cap = views[mid]["capability"] if mid in views else 0
        if ev["kind"] == "new_offer" and alert_mod.is_free_offer(o):
            new_free.append(ev)
            add(mid, cap + 20, f"new free access on {o.get('provider_name')}")
        elif ev["kind"] == "changed" and ev.get("field") == "price" and o.get("role") != "reseller":
            try:
                ob = (3 * ev["old"]["in"] + ev["old"]["out"]) / 4
                nb = (3 * ev["new"]["in"] + ev["new"]["out"]) / 4
            except (KeyError, TypeError):
                continue
            if ob and nb < ob:
                prices.append(ev)
                pct = round(100 * (1 - nb / ob))
                if pct >= 10:
                    add(mid, cap + pct / 3, f"{pct}% cheaper on {o.get('provider_name')}")
            elif ob and nb > ob:
                prices.append(ev)
        elif ev["kind"] == "changed" and ev.get("field") == "terms":
            prices.append(ev)
        elif ev["kind"] == "offer_removed" and alert_mod.is_free_offer(ev.get("old")):
            ended.append(ev)
    for v in board.get("free_models") or []:
        add(v["model"]["id"], v["capability"], "still free: " + respond.render.short_access(v))
    for v in board.get("cheapest_premium") or []:
        add(v["model"]["id"], v["capability"] - 5, "premium model below list price" + premium_extra(v))
    top = sorted(opps.items(), key=lambda kv: -kv[1][0])[: int(s["weekly_top"])]
    return {"events": events, "top": top, "new_free": new_free, "prices": prices, "ended": ended, "board": board,
            "views": views, "hours": hours}


def render_weekly(report, db, cfg):
    end = now_utc()
    days = round(report["hours"] / 24, 1)
    blocks = [f"<b>📅 WEEKLY MODEL ACCESS REPORT</b>\n<i>{end:%-d %b %Y} · last {days:g} days · "
              f"{len(report['events'])} changes tracked</i>"]
    lines = []
    for i, (mid, (_, why)) in enumerate(report["top"], 1):
        v = report["views"][mid]
        lines.append(f"{i}. <b>{e(v['model']['name'])}</b> · {e(why)}\n   <i>/modelaccess {e(mid.split('/', 1)[1])}</i>")
    blocks.append("<b>⭐ BEST MODEL ACCESS OPPORTUNITIES THIS WEEK</b>\n" + ("\n".join(lines) or "<i>None this week.</i>"))
    if report["new_free"]:
        blocks.append("<b>🆕 New free access</b>\n" + "\n".join(f"• {e(change_text(ev, db))}" for ev in report["new_free"][:10]))
    if report["prices"]:
        blocks.append("<b>💲 Price and limit changes</b>\n" + "\n".join(f"• {e(change_text(ev, db))}" for ev in report["prices"][:10]))
    if report["ended"]:
        blocks.append("<b>⛔ Free access that ended</b>\n" + "\n".join(f"• {e(change_text(ev, db))}" for ev in report["ended"][:8]))
    board = report["board"]
    snap = []
    for key, title in (("free_models", "Best completely free"), ("coding", "Coding"), ("reasoning", "Reasoning"),
                       ("agents", "Agents"), ("cheapest_premium", "Cheapest premium"), ("value", "Best value")):
        if board.get(key):
            v = board[key][0]
            snap.append(f"• <b>{title}:</b> {e(v['model']['name'])} ({e(respond.render.short_access(v))})")
    if board.get("free_providers"):
        snap.append(f"• <b>Best free API provider:</b> {e(board['free_providers'][0]['name'])}")
    if snap:
        blocks.append("<b>🏆 Leaderboard (full list: /modelaccess leaderboard)</b>\n" + "\n".join(snap))
    leads = [x for x in db.leads.values() if (hours_since(x.get("first_seen")) or 1e9) <= report["hours"]][:5]
    if leads:
        blocks.append("<b>⚠️ UNVERIFIED leads to check</b>\n" + "\n".join(
            f"• {msg.link(msg.clip(x['title'], 120), x['url'])} <i>({e(x['source'])})</i>" for x in leads))
    blocks.append(checked_line(db, cfg["settings"]))
    return finish(blocks)


def weekly(config, dry_run=False):
    from ..deliver import deliver

    cfg = mconfig.load()
    db = AccessDB()
    if not db.models:
        print("The model access database is empty; run `python -m agent model-access` first.")
        return 0
    report = weekly_report(db, cfg)
    messages = render_weekly(report, db, cfg)
    for m in messages:
        print(msg.strip_html(m) + "\n")
    if dry_run:
        print("(dry run: nothing sent or saved)")
        return 0
    subject = f"Weekly Model Access Report: {len(report['top'])} opportunities"
    sent, errors = deliver(config, messages, (subject, "<br>".join(m.replace("\n", "<br>") for m in messages),
                                              "\n\n".join(msg.strip_html(m) for m in messages)), silent=True)
    for err in errors:
        print(f"Failed: {err}")
    if not sent:
        return 1 if errors else 0
    db.meta["weekly_last"] = now_iso()
    weekly_path().parent.mkdir(exist_ok=True)
    weekly_path().write_text(json.dumps(messages, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    try:
        bundle, _, _ = respond.build(db, cfg)  # so /modelaccess weekly in the instant bot shows it at once
        push_bot(db, bundle)
    except Exception as ex:  # noqa: BLE001
        print(f"  bot not updated: {str(ex)[:120]}")
    db.save()
    return 0

