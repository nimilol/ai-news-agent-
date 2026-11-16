"""Picks, comparisons, alternatives and the personal leaderboard, all computed from the database (no AI).

Capability is a transparent proxy, not a benchmark: how the maker prices the model (premium models cost more),
reasoning and tool calling, context length, how recent it is, and how many providers chose to host it. Rankings
weigh that against price, so a list is never "the provider with the most models".
"""

import math
import re
from datetime import date

from .normalize import CONFIRMED, FREE_STATUSES, agent_cost, heavy, log_price, open_weights_offer

HOSTED_FREE = ("LIMITED_FREE", "FREE_CREDITS")
NOT_CHAT = re.compile(r"guard|safety|rerank|embed|ocr|moderation|classifier", re.I)
PREMIUM_MIN_BLENDED = 2.5


def confirmed(o):
    return o.get("verification") in CONFIRMED and o.get("active", True)


def offers_for(db, mid, by_model=None):
    offers = list((by_model or db.offers_by_model()).get(mid, []))
    m = db.models.get(mid) or {}
    if m.get("open_weights") and m.get("company") != "unattributed":
        offers.append(open_weights_offer(m))
    return offers


def paid(offers):
    """Confirmed per-token prices from the official API and vetted providers (unvetted resellers are only shown in
    comparisons, never picked)."""
    return [o for o in offers if confirmed(o) and o.get("access") == "paid_api" and o.get("blended") is not None
            and o.get("role") != "reseller"]


def official_offer(offers):
    off = [o for o in paid(offers) if o.get("role") == "official"]
    return min(off, key=lambda o: o["blended"]) if off else None


def ref_price(offers):
    """The model's list price: the official API, else the median listed price."""
    off = official_offer(offers)
    if off:
        return off["blended"]
    prices = sorted(o["blended"] for o in paid(offers))
    return prices[len(prices) // 2] if prices else None


def free_rank(o):
    ver = {"OFFICIAL": 0, "VERIFIED": 1, "CATALOG": 2}.get(o.get("verification"), 9)
    stated = 0 if (o.get("terms") or {}).get("daily_limit") or (o.get("terms") or {}).get("rate_limit") else 1
    return (o.get("access") == "open_weights", ver, stated, -(o.get("context") or 0))


def best_free(offers, hosted_only=False):
    free = [o for o in offers if confirmed(o) and o["status"] in FREE_STATUSES and o.get("role") != "reseller"
            and not (hosted_only and o.get("access") == "open_weights")]
    return min(free, key=free_rank) if free else None


def unverified_free(offers):
    return [o for o in offers if o.get("verification") == "UNVERIFIED" and (o.get("price") or {}).get("out") == 0]


def subscriptions(offers):
    return [o for o in offers if o.get("access") == "subscription" and o.get("active", True)]


def capability(m, offers, today=None):
    today = today or date.today()
    score = 0.0
    rp = ref_price(offers)
    if rp:
        score += min(30.0, 8 * math.log2(1 + rp))
    score += 12 if m.get("reasoning") else 0
    score += 8 if m.get("tool_call") else 0
    ctx = m.get("context") or 0
    if ctx > 32000:
        score += min(12.0, 4 * math.log2(ctx / 32000))
    try:
        age = (today - date.fromisoformat(str(m.get("release_date"))[:10])).days
        score += 15 if age <= 60 else 10 if age <= 180 else 5 if age <= 365 else 0
    except ValueError:
        pass
    score += min(12.0, 3 * math.log2(1 + (m.get("providers_listed") or 1)))
    if "small" in (m.get("categories") or []):
        score -= 5
    return round(score, 1)


def picks(m, offers, settings):
    """The per-model answers: best free option, cheapest confirmed provider, best value for developers, best for
    agents, best for heavy usage."""
    p = sorted(paid(offers), key=lambda o: o.get("role") != "official")  # on a price tie, the official API wins
    ctx = m.get("context") or 0
    out = {"best_free": best_free(offers, hosted_only=True) or best_free(offers),
           "official": official_offer(offers), "cheapest": min(p, key=lambda o: o["blended"]) if p else None}
    # developers: the cheapest provider that keeps most of the context window and supports tool calls
    dev = [o for o in p if (o.get("context") or 0) >= 0.75 * ctx and (o.get("tool_call") or not m.get("tool_call"))]
    out["dev_value"] = min(dev, key=lambda o: o["blended"]) if dev else out["cheapest"]
    agents = [o for o in p if o.get("tool_call") and (o.get("context") or 0) >= settings["min_context_for_agents"]]
    out["agents"] = min(agents, key=lambda o: agent_cost(o["price"])) if agents else None
    ratio = settings.get("heavy_usage_ratio", 3)
    out["heavy"] = min(p, key=lambda o: heavy(o["price"], ratio)) if p else None
    return out


def model_view(db, mid, settings, by_model=None):
    m = db.models[mid]
    offers = offers_for(db, mid, by_model)
    return {"model": m, "offers": offers, "picks": picks(m, offers, settings), "capability": capability(m, offers),
            "ref_price": ref_price(offers)}


def all_views(db, settings):
    by_model = db.offers_by_model()
    return {mid: model_view(db, mid, settings, by_model) for mid in db.models
            if by_model.get(mid) or db.models[mid].get("open_weights")}


def value_score(view, weight=8):
    cheapest = view["picks"]["cheapest"]
    price = 0.0 if view["picks"]["best_free"] and view["picks"]["best_free"]["access"] != "open_weights" else \
        (cheapest["blended"] if cheapest else None)
    if price is None:
        return None
    return round(view["capability"] - weight * log_price(price), 1)


def _top(items, key, n):
    items = [i for i in items if key(i) is not None]
    return sorted(items, key=key, reverse=True)[:n]


def leaderboard(views, settings, n=5):
    text = [v for v in views.values() if {"general", "reasoning", "coding"} & set(v["model"].get("categories") or [])]
    hosted_free = [v for v in text if best_free(v["offers"], hosted_only=True)]
    board = {"free_models": _top(hosted_free, lambda v: v["capability"], n)}
    # providers: quality of what they give away (mean capability of their 3 best free models), plus confirmed terms
    prov = {}
    for v in hosted_free:
        for o in v["offers"]:
            if confirmed(o) and o["status"] in HOSTED_FREE:
                prov.setdefault(o["provider"], {"name": o.get("provider_name"), "offers": [], "terms": o.get("terms"),
                                                "verification": o.get("verification")})
                prov[o["provider"]]["offers"].append((v["capability"], v))
    rows = []
    for pid, p in prov.items():
        best = sorted(p["offers"], key=lambda x: -x[0])[:3]
        score = sum(c for c, _ in best) / len(best)
        score += 8 if p["verification"] in ("OFFICIAL", "VERIFIED") else 0
        score += 4 if (p["terms"] or {}).get("daily_limit") or (p["terms"] or {}).get("rate_limit") else 0
        rows.append({"provider": pid, "name": p["name"], "score": round(score, 1), "models": [v for _, v in best],
                     "count": len(p["offers"]), "terms": p["terms"], "verification": p["verification"]})
    board["free_providers"] = sorted(rows, key=lambda r: -r["score"])[:n]
    coding = [v for v in text if "coding" in v["model"]["categories"] or (v["model"].get("tool_call") and v["model"].get("reasoning"))]
    board["coding"] = _top(coding, lambda v: None if value_score(v) is None else
                           value_score(v) + (10 if "coding" in v["model"]["categories"] else 0), n)
    board["reasoning"] = _top([v for v in text if v["model"].get("reasoning")], value_score, n)
    agents = [v for v in text if "agentic" in v["model"]["categories"]]
    board["agents"] = _top(agents, lambda v: None if not (v["picks"]["agents"] or best_free(v["offers"], True)) else
                           v["capability"] - 6 * log_price(0 if best_free(v["offers"], True) else
                                                           agent_cost(v["picks"]["agents"]["price"])), n)
    premium = []
    for v in text:
        rp = v["ref_price"]
        if not rp or rp < PREMIUM_MIN_BLENDED:
            continue
        free = best_free(v["offers"], hosted_only=True)
        cheapest = v["picks"]["cheapest"]
        price = 0.0 if free else (cheapest["blended"] if cheapest else None)
        if price is None or price >= rp:
            continue
        premium.append({**v, "saving": round(100 * (1 - price / rp)), "access_price": price,
                        "access_offer": free or cheapest})
    board["cheapest_premium"] = sorted(premium, key=lambda v: (-v["capability"], v["access_price"]))[:n]
    board["value"] = _top(text, value_score, n)
    return board


def alternatives(views, mid, n=6):
    """Similar models (same main category, close capability) with free access or a lower price than this one."""
    me = views.get(mid)
    if not me:
        return []
    cats = set(me["model"].get("categories") or [])
    main = next((c for c in ("coding", "reasoning", "image", "video", "audio", "embedding", "general") if c in cats), None)
    my_price = me["picks"]["cheapest"]["blended"] if me["picks"]["cheapest"] else None
    my_free = bool(best_free(me["offers"], hosted_only=True))
    out = []
    for other_id, v in views.items():
        if other_id == mid or (main and main not in (v["model"].get("categories") or [])):
            continue
        if v["capability"] < me["capability"] - 25:
            continue
        free = best_free(v["offers"], hosted_only=True)
        cheapest = v["picks"]["cheapest"]
        if free and not my_free:
            reason = "free access"
        elif cheapest and my_price and cheapest["blended"] < my_price * 0.8:
            reason = f"{round(100 * (1 - cheapest['blended'] / my_price))}% cheaper"
        elif not my_price and not my_free and (free or cheapest):
            reason = "available to use"
        else:
            continue
        out.append({**v, "reason": reason})
    out.sort(key=lambda v: (-(v["capability"] - 4 * log_price(0 if best_free(v["offers"], True) else
                                                               (v["picks"]["cheapest"] or {}).get("blended") or 0))))
    return out[:n]


def cheapest_by_category(views, n=3):
    out = {}
    for cat in ("general", "reasoning", "coding", "multimodal", "agentic", "small", "image", "audio", "embedding"):
        vs = [v for v in views.values() if cat in (v["model"].get("categories") or []) and v["picks"]["cheapest"]
              and (cat == "embedding" or not NOT_CHAT.search(v["model"]["name"]))]
        if not vs:
            continue
        caps = sorted(v["capability"] for v in vs)
        floor = caps[len(caps) // 2]  # the cheapest of the better half, not the tiniest model
        good = [v for v in vs if v["capability"] >= floor]
        out[cat] = {"cheapest": min(vs, key=lambda v: v["picks"]["cheapest"]["blended"]),
                    "best": sorted(good, key=lambda v: v["picks"]["cheapest"]["blended"])[:n]}
    return out


def rank_order(views):
    """Index order for name lookups: models with access first, then capability, undated ids before dated ones."""
    def key(v):
        mid = v["model"]["id"]
        dated = bool(re.search(r"-\d{4}(-\d{2}){0,2}$|-\d{6,8}$", mid))
        return (dated, -v["capability"], len(mid))

    return [v["model"]["id"] for v in sorted(views.values(), key=key)]
