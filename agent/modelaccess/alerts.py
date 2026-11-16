"""Free-access alerts: turn this refresh's change events into a short Telegram batch.

Alert kinds (each needs confirmed data; UNVERIFIED offers never trigger an alert):
    PAID_TO_FREE     a model that had no confirmed free access now has some
    NEW_FREE_OFFER   a new confirmed free offer for a model (a new provider gives it away)
    NEW_FREE_MODEL   a model seen for the first time already has confirmed free access
    NEW_PROVIDER     a vetted provider starts offering a premium model at least 15% under its cheapest price
    PRICE_DROP       a confirmed price on a vetted provider fell by 25% or more
    QUOTA_CHANGE     a free offer's published limits changed
    FREE_ENDED       a confirmed free offer disappeared (its catalog loaded fine twice without it)
    NEW_PLATFORM     a platform / app / credits entry was confirmed on its source page
    DISCONTINUED     a platform's source page confirms it was discontinued

Every alert carries the model, provider, what changed, the previous and new conditions, quota or pricing, a link,
the source and the verification date. Each one has a key (kind + offer + new condition); a key already in the
alert history within `alert_cooldown_days` is never sent again.
"""

import hashlib
import json

from .. import messages as msg
from .analyze import PREMIUM_MIN_BLENDED, capability, offers_for, paid, ref_price
from .db import hours_since, now_iso
from .normalize import CONFIRMED, FREE_STATUSES
from .render import PREFIX, e, money, price_text, ver_text

TITLES = {"PAID_TO_FREE": "Paid → free", "NEW_FREE_OFFER": "New free access", "NEW_FREE_MODEL": "New model with free access",
          "NEW_PROVIDER": "New cheaper provider", "PRICE_DROP": "Price drop", "QUOTA_CHANGE": "Free limits changed",
          "FREE_ENDED": "Free access ended", "NEW_PLATFORM": "New free platform or credits",
          "DISCONTINUED": "Discontinued"}


def _key(kind, ref, sig):
    return f"{kind}:{ref}:" + hashlib.sha1(json.dumps(sig, sort_keys=True, default=str).encode()).hexdigest()[:10]


def _is_free(o):
    return bool(o) and o.get("status") in FREE_STATUSES and o.get("verification") in CONFIRMED \
        and o.get("access") != "open_weights" and o.get("role") != "reseller"


def _terms_text(t):
    t = t or {}
    return "; ".join(x for x in (t.get("rate_limit"), t.get("daily_limit"), t.get("monthly_limit"),
                                 t.get("token_quota")) if x) or "limits not stated"


def build(db, events, cfg, platform_events=()):
    """Alert candidates for this run, best first, before duplicate filtering."""
    by_model = db.offers_by_model()
    out = []
    free_before = {}  # model -> had confirmed hosted free access before this run's new offers
    for ev in events:
        if ev["kind"] == "new_offer" and _is_free(ev.get("new")):
            free_before.setdefault(ev["model"], False)
    for mid in list(free_before):
        others = [o for o in by_model.get(mid, []) if _is_free(o)
                  and not any(ev.get("offer") == o["id"] and ev["kind"] == "new_offer" for ev in events)]
        free_before[mid] = bool(others)
    new_models = {ev["model"] for ev in events if ev["kind"] == "new_model"}

    for ev in events:
        mid = ev.get("model")
        m = db.models.get(mid) or {}
        offers = offers_for(db, mid, by_model) if mid else []
        cap = capability(m, offers) if m else 0
        o = db.offers.get(ev.get("offer") or "") or {}
        base = {"model": mid, "model_name": m.get("name") or mid, "offer": o.get("id"),
                "provider": o.get("provider_name") or o.get("provider"), "url": o.get("url"),
                "source": o.get("source"), "verified": o.get("last_verified"), "verification": o.get("verification"),
                "capability": cap}
        kind = None
        if ev["kind"] == "new_offer" and _is_free(o):
            kind = "NEW_FREE_MODEL" if mid in new_models else "PAID_TO_FREE" if not free_before.get(mid) else "NEW_FREE_OFFER"
            before = "no confirmed free access" if kind != "NEW_FREE_OFFER" else "free elsewhere only"
            cheapest = min((x["blended"] for x in paid(offers)), default=None)
            if kind == "PAID_TO_FREE" and cheapest:
                before = f"paid only, from {money(cheapest)} blended per 1M tokens"
            out.append({**base, "kind": kind, "before": before, "now": f"free · {_terms_text(o.get('terms'))}",
                        "pricing": _terms_text(o.get("terms")), "sig": {"status": o.get("status")},
                        "priority": "P1" if (kind != "NEW_FREE_OFFER" and (ref_price(offers) or 0) >= 1) else "P2"})
        elif ev["kind"] == "new_offer" and o.get("access") == "paid_api" and o.get("role") in ("third_party", "official") \
                and o.get("verification") in CONFIRMED:
            rp = ref_price(offers) or 0
            older = [x["blended"] for x in paid(offers) if x["id"] != o["id"]]
            if rp >= PREMIUM_MIN_BLENDED and older and o.get("blended") is not None and o["blended"] <= 0.85 * min(older):
                out.append({**base, "kind": "NEW_PROVIDER", "before": f"cheapest was {money(min(older))} blended",
                            "now": f"{money(o['blended'])} blended ({price_text(o)})", "pricing": price_text(o),
                            "sig": {"price": o.get("price")}, "priority": "P2"})
        elif ev["kind"] == "changed" and ev.get("field") == "price" and o.get("role") != "reseller" \
                and o.get("verification") in CONFIRMED:
            old, new = ev.get("old") or {}, ev.get("new") or {}
            try:
                ob, nb = (3 * old["in"] + old["out"]) / 4, (3 * new["in"] + new["out"]) / 4
            except (KeyError, TypeError):
                continue
            if ob > 0 and nb <= 0.75 * ob and nb > 0:
                out.append({**base, "kind": "PRICE_DROP", "before": price_text({"price": old}),
                            "now": price_text({"price": new}), "pricing": f"{round(100 * (1 - nb / ob))}% lower (blended)",
                            "sig": {"price": new}, "priority": "P2" if cap >= 40 else "P3"})
        elif ev["kind"] == "changed" and ev.get("field") == "terms" and _is_free(o):
            out.append({**base, "kind": "QUOTA_CHANGE", "before": _terms_text(ev.get("old")),
                        "now": _terms_text(ev.get("new")), "pricing": _terms_text(ev.get("new")),
                        "sig": {"terms": ev.get("new")}, "priority": "P2"})
        elif ev["kind"] == "offer_removed" and _is_free(ev.get("old")):
            old = ev["old"]
            out.append({**base, "provider": old.get("provider_name"), "kind": "FREE_ENDED",
                        "before": f"free · {_terms_text(old.get('terms'))}", "now": "no longer listed",
                        "pricing": "", "sig": {"ended": ev["t"][:10]}, "priority": "P2"})
    for pev in platform_events:
        p = pev["platform"]
        kind = "DISCONTINUED" if pev["new"] == "DISCONTINUED" else "NEW_PLATFORM" if pev["new"] in FREE_STATUSES else None
        if not kind:
            continue
        out.append({"kind": kind, "model": None, "model_name": p.get("models") or p["name"], "offer": f"platform:{p['id']}",
                    "provider": p["name"], "url": p.get("url"), "source": p.get("url"), "verified": pev.get("verified"),
                    "verification": "VERIFIED", "before": pev.get("old") or "not tracked", "now": p.get("limits") or pev["new"],
                    "pricing": p.get("limits") or "", "sig": {"status": pev["new"]}, "capability": 50,
                    "priority": "P1" if kind == "NEW_PLATFORM" else "P2"})
    rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    out.sort(key=lambda a: (rank[a["priority"]], -a["capability"]))
    return out


def fresh(db, candidates, cfg):
    """Drop alerts already sent within the cooldown (by key), keep at most max_alerts_per_run."""
    days = cfg["settings"]["alert_cooldown_days"]
    queued = {k for n in db.outbox.values() if n["status"] in ("PENDING", "RETRY_PENDING") for k in n.get("keys") or []}
    keep, seen = [], set(queued)
    for a in candidates:
        a["key"] = _key(a["kind"], a.get("offer") or a.get("model"), a.get("sig"))
        prev = db.alerts.get(a["key"])
        if a["key"] in seen or (prev and (hours_since(prev.get("at")) or 1e9) <= days * 24):
            continue
        seen.add(a["key"])
        keep.append(a)
    return keep[: int(cfg["settings"]["max_alerts_per_run"])]


def card(a):
    emoji = msg.PRIORITIES[a["priority"]][0]
    lines = [f"{emoji} {a['priority']} · <b>{e(a['model_name'])}</b> · <i>{e(TITLES[a['kind']])}</i>"]
    rows = [("Provider", a.get("provider")), ("What changed", TITLES[a["kind"]]), ("Before", a.get("before")),
            ("Now", a.get("now")), ("Quota / pricing", a.get("pricing")), ("Source", a.get("source"))]
    lines += [f"<b>{k}:</b> {e(str(v))}" for k, v in rows if v]
    ver = ver_text({"verification": a.get("verification"), "last_verified": a.get("verified")})
    lines.append(f"<b>Verification:</b> {e(ver)}")
    if a.get("url"):
        lines.append("🔗 " + msg.link(a["url"].replace("https://", "")[:60], a["url"]))
    if a.get("model"):
        lines.append(f"<i>/modelaccess {e(a['model'].split('/', 1)[1])}</i>")
    return "\n".join(lines)


def render(alerts):
    head = (f"<b>🔔 MODEL ACCESS ALERT</b> · {msg.human_date(now_iso()[:10])}\n"
            f"<i>{len(alerts)} change{'s' if len(alerts) != 1 else ''} in where models can be used for free or cheaply. "
            "Free tiers always have limits.</i>")
    parts = msg.paginate([(head, None)] + [(card(a), None) for a in alerts], 3800)
    return [f"{PREFIX}\n{p}" for p in parts]


def queue(db, alerts):
    """Put one batch in the outbox (delivered through the release monitor's confirmed-delivery code)."""
    if not alerts:
        return None
    nid = "ma-" + hashlib.sha1("|".join(a["key"] for a in alerts).encode()).hexdigest()[:12]
    if nid in db.outbox:
        return None
    best = min((a["priority"] for a in alerts), key=lambda p: msg.PRIORITY_RANK[p])
    db.outbox[nid] = {"id": nid, "release_id": nid, "kind": "model_access", "category": "MODEL_ACCESS",
                      "model_name": "model access", "parts": render(alerts), "sent_parts": {}, "status": "PENDING",
                      "attempts": 0, "created": now_iso(), "priority": best, "sent_at": None, "last_error": None,
                      "log": [], "receipt": {"status": "NOT_NEEDED"}, "keys": [a["key"] for a in alerts],
                      "alerts": [{k: a.get(k) for k in ("kind", "model", "offer", "provider", "before", "now")} for a in alerts]}
    return nid


def mark_sent(db):
    """Alert keys count as sent only once Telegram confirmed their batch."""
    for n in db.outbox.values():
        if n["status"] == "SENT":
            for k in n.get("keys") or []:
                db.alerts.setdefault(k, {"at": n.get("sent_at") or now_iso(), "batch": n["id"]})


def is_free_offer(o):
    return _is_free(o)
