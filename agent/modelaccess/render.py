"""Telegram messages for Model Access Intelligence. Every message (every part of a long reply) begins with
[MODEL ACCESS INTELLIGENCE], so it is never confused with /free or any other feature.

Facts only come from the database: anything not stated by a source is shown as "not stated", a limited free tier
is never called unlimited, and unconfirmed offers always carry the UNVERIFIED label.
"""

from .. import messages as msg
from .analyze import best_free, confirmed, subscriptions, unverified_free
from .normalize import ACCESS_TYPES

PREFIX = "[MODEL ACCESS INTELLIGENCE]"
LIMIT = 3800
e = msg.e
STATUS = {"FREE": ("🟢", "FREE (self-hosted)"), "LIMITED_FREE": ("🟢", "LIMITED FREE"),
          "FREE_CREDITS": ("🎁", "FREE CREDITS"), "LOW_COST": ("🔵", "LOW COST"), "PAID_ONLY": ("⚪", "PAID ONLY"),
          "UNVERIFIED": ("⚠️", "UNVERIFIED"), "DISCONTINUED": ("⛔", "DISCONTINUED"), None: ("⚪", "NO PRICE LISTED")}
VERIFICATION = {"OFFICIAL": "✅ official pricing page", "VERIFIED": "✅ provider terms re-checked",
                "CATALOG": "📋 provider catalog listing", "UNVERIFIED": "⚠️ UNVERIFIED"}
CATEGORY_LABEL = {"general": "General", "reasoning": "Reasoning", "coding": "Coding", "multimodal": "Multimodal",
                  "agentic": "Agents", "small": "Small models", "image": "Image", "audio": "Audio",
                  "video": "Video", "embedding": "Embeddings"}


def finish(blocks):
    """Paginate, then put the identity line first in every part."""
    return [f"{PREFIX}\n{m}" for m in msg.paginate([(b, None) for b in blocks if b], LIMIT)]


def money(x):
    if x is None:
        return "n/a"
    if x == 0:
        return "$0"
    if x < 0.01:
        return f"${x:.4f}".rstrip("0")
    if x < 1:
        return f"${x:.3f}".rstrip("0").rstrip(".")
    return f"${x:,.2f}"


def price_text(o):
    p = o.get("price") or {}
    if o.get("access") == "subscription":
        return "included in the plan" if not p else f"{money(p.get('in'))} in / {money(p.get('out'))} out (plan pricing)"
    if p.get("in") == 0 and p.get("out") == 0:
        return "free"
    text = f"{money(p.get('in'))} in / {money(p.get('out'))} out"
    if p.get("cache") is not None:
        text += f" · cached in {money(p['cache'])}"
    return text


def status_text(status):
    emoji, label = STATUS.get(status, STATUS[None])
    return f"{emoji} {label}"


def ver_text(o):
    text = VERIFICATION.get(o.get("verification"), "⚠️ UNVERIFIED")
    if o.get("last_verified") and o.get("verification") != "UNVERIFIED":
        text += f", {msg.human_date(o['last_verified'])}"
    return text


def ctx_text(n):
    if not n:
        return "not stated"
    return f"{n / 1_000_000:g}M tokens" if n >= 1_000_000 else f"{round(n / 1000):g}K tokens"


def company_name(cfg, key):
    c = (cfg.get("companies") or {}).get(key) or {}
    return c.get("name") or ("Unattributed (provider-only listing)" if key == "unattributed" else key.replace("-", " ").title())


def checked_line(db, settings):
    last = db.meta.get("last_refresh")
    if not last:
        return "<i>No refresh has completed yet.</i>"
    age = db.age_hours() or 0
    text = f"Last checked {last[:16].replace('T', ' ')} UTC"
    if age > settings.get("stale_hours", 12):
        text += f" ({round(age)} hours ago: the next refresh updates it)"
    bad = [k for k, s in db.meta.get("sources", {}).items() if not s.get("ok") and k in ("models_dev", "openrouter", "gemini")]
    if bad:
        text += f" · not loaded last time: {', '.join(bad)} (older data kept)"
    return f"<i>{e(text)}.</i>"


def offer_line(o, show_status=True):
    bits = [f"<b>{e(o.get('provider_name') or o['provider'])}</b>", e(price_text(o))]
    if o.get("blended") and o.get("access") == "paid_api":
        bits.append(f"blended {money(o['blended'])}")
    if o.get("context"):
        bits.append(e(ctx_text(o["context"])))
    if show_status:
        bits.append(status_text(o["status"]))
    bits.append(e(ver_text(o)))
    if o.get("role") == "reseller":
        bits.append("<i>unvetted reseller</i>")
    return " · ".join(bits)


def free_details(o, m):
    t = o.get("terms") or {}
    ns = "not stated"
    rows = [("Platform", o.get("provider_name") or o["provider"]),
            ("Exact model", o.get("listing") or m["id"]),
            ("Access type", ACCESS_TYPES.get(o.get("access"), o.get("access"))),
            ("Registration", t.get("registration") or ns),
            ("Card required", t.get("card") or ns),
            ("Daily limit", t.get("daily_limit") or ns),
            ("Monthly limit", t.get("monthly_limit") or ns),
            ("Token quota", t.get("token_quota") or ns),
            ("Rate limits", t.get("rate_limit") or ns),
            ("Context", ctx_text(o.get("context") or m.get("context"))),
            ("Geography", t.get("geography") or ns),
            ("Commercial use", t.get("commercial_use") or ns),
            ("Permanent or temporary", t.get("permanence") or ns)]
    lines = [f"<b>{label}:</b> {e(str(v))}" for label, v in rows]
    if t.get("notes"):
        lines.append(f"<b>Notes:</b> {e(t['notes'])}")
    lines.append(f"<b>Verification:</b> {e(ver_text(o))}")
    if o.get("url"):
        lines.append(f"🔗 {msg.link(o['url'].replace('https://', '')[:60], o['url'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------- per model
def model_head(view, cfg):
    m = view["model"]
    cats = ", ".join(CATEGORY_LABEL.get(c, c) for c in m.get("categories") or [])
    bits = [m["id"], cats, f"context {ctx_text(m.get('context'))}"]
    if m.get("release_date"):
        bits.append(f"released {msg.human_date(m['release_date'])}")
    return (f"<b>🔑 {e(m['name'])}</b> · {e(company_name(cfg, m['company']))}\n"
            f"<i>{e(' · '.join(b for b in bits if b))}</i>\n"
            f"<b>Status:</b> {status_text(m.get('status'))}")


def render_model(view, db, cfg):
    m, p, offers = view["model"], view["picks"], view["offers"]
    s = cfg["settings"]
    lines = [model_head(view, cfg)]

    def pick(label, o, extra=""):
        if o and label != "Cheapest verified provider" and p["cheapest"] and o["id"] == p["cheapest"]["id"]:
            lines.append(f"<b>{label}:</b> <i>the cheapest provider above</i>")
            return
        lines.append(f"<b>{label}:</b> " + (offer_line(o, show_status=False) + extra if o else "<i>none confirmed</i>"))

    pick("Best free option", p["best_free"])
    pick("Cheapest verified provider", p["cheapest"])
    if p["official"] and (not p["cheapest"] or p["official"]["id"] != p["cheapest"]["id"]):
        pick("Official API price", p["official"])
    elif p["official"]:
        lines.append("<b>Official API price:</b> <i>the cheapest provider above</i>")
    pick("Best value for developers", p["dev_value"], " <i>(keeps the context window and tool calls)</i>")
    if p["agents"]:
        pick("Best for agents", p["agents"], " <i>(lowest cost with cached input and tool calls)</i>")
    pick("Best for heavy usage", p["heavy"], " <i>(output-heavy workloads)</i>")
    subs = subscriptions(offers)
    if subs:
        lines.append("<b>Included in subscriptions:</b> " + e(", ".join(o.get("provider_name") or o["provider"] for o in subs[:5])))
    if m.get("open_weights"):
        lines.append("<b>Open weights:</b> yes: free to download and self-host (you supply the hardware; check the licence)")
    blocks = ["\n".join(lines)]
    free = p["best_free"]
    if free and free.get("access") != "open_weights":
        blocks.append("<b>🆓 Free access details</b>\n" + free_details(free, m))
    unv = unverified_free(offers)
    if unv:
        blocks.append("<b>⚠️ UNVERIFIED free listings</b> (not confirmed: don't rely on them)\n" + "\n".join(
            f"• {e(o.get('provider_name') or o['provider'])}: {e(o.get('note') or 'listed at $0')}" for o in unv[:5]))
    blocks.append(f"<i>Prices in USD per 1M tokens; blended = 3 parts input to 1 part output.</i>\n"
                  f"<i>More: /modelaccess compare {e(m['id'].split('/', 1)[1])} · /modelaccess alternatives "
                  f"{e(m['id'].split('/', 1)[1])}</i>\n{checked_line(db, s)}")
    return finish(blocks)


def render_compare(view, db, cfg):
    m, offers = view["model"], view["offers"]
    rows = sorted((o for o in offers if o.get("access") != "subscription"),
                  key=lambda o: (o.get("verification") == "UNVERIFIED", o.get("blended") is None, o.get("blended") or 0,
                                 o.get("access") == "open_weights"))
    head = (f"<b>⚖️ ACCESS COMPARISON · {e(m['name'])}</b>\n<i>{e(m['id'])} · {len(rows)} options, cheapest first "
            f"(free first)</i>")
    lines = [f"{i}. {offer_line(o)}" for i, o in enumerate(rows[:25], 1)]
    if len(rows) > 25:
        lines.append(f"<i>…and {len(rows) - 25} more.</i>")
    blocks = [head, "\n".join(lines) or "<i>No offers recorded.</i>"]
    via = [o for o in offers if o.get("routes")]
    for o in via[:1]:
        blocks.append(f"<b>Via OpenRouter:</b> {len(o['routes'])} upstream providers checked " + e(
            "(" + ", ".join(f"{r['name']} {money(r.get('in'))}/{money(r.get('out'))}" for r in o["routes"][:4]) + ")"))
    subs = subscriptions(offers)
    if subs:
        blocks.append("<b>💳 Subscriptions (priced separately)</b>\n" + "\n".join(
            f"• {e(o.get('provider_name') or o['provider'])}: {e(price_text(o))}" for o in subs))
    blocks.append("<i>Prices in USD per 1M tokens; blended = 3 parts input to 1 part output. Listed prices can differ "
                  "by region, tier and context length.</i>\n" + checked_line(db, cfg["settings"]))
    return finish(blocks)


def short_access(v):
    free = best_free(v["offers"], hosted_only=True)
    if free:
        return f"free on {free.get('provider_name')}"
    c = v["picks"]["cheapest"]
    if c:
        return f"from {money(c['blended'])} blended on {c.get('provider_name')}"
    if v["model"].get("open_weights"):
        return "open weights (self-host)"
    return "no confirmed price"


def render_alternatives(view, alts, db, cfg):
    m = view["model"]
    head = f"<b>🔁 ALTERNATIVES TO {e(m['name'].upper())}</b>\n<i>Similar models that are free or cheaper to use.</i>"
    if not alts:
        body = "<i>No similar model with free or clearly cheaper access was found.</i>"
    else:
        body = "\n".join(f"{i}. <b>{e(v['model']['name'])}</b> ({e(company_name(cfg, v['model']['company']))}) · "
                         f"{e(v['reason'])} · {e(short_access(v))}\n   <i>/modelaccess {e(v['model']['id'].split('/', 1)[1])}</i>"
                         for i, v in enumerate(alts, 1))
    return finish([head, body, "<i>Similarity: same main category and a close capability score (maker's price "
                               "tier, reasoning, tools, context, recency). Check quality for your task.</i>\n"
                   + checked_line(db, cfg["settings"])])


def render_company(key, views, db, cfg, n=15):
    mine = sorted((v for v in views.values() if v["model"]["company"] == key), key=lambda v: -v["capability"])
    name = company_name(cfg, key)
    head = f"<b>🏢 {e(name.upper())} · MODEL ACCESS</b>\n<i>{len(mine)} models found in the catalogs; best first.</i>"
    lines = [f"{i}. <b>{e(v['model']['name'])}</b> · {status_text(v['model'].get('status'))} · {e(short_access(v))}\n"
             f"   <i>/modelaccess {e(v['model']['id'].split('/', 1)[1])}</i>" for i, v in enumerate(mine[:n], 1)]
    if len(mine) > n:
        lines.append(f"<i>…and {len(mine) - n} more: ask for one by name.</i>")
    return finish([head, "\n".join(lines) or "<i>No models found.</i>", checked_line(db, cfg["settings"])])


# ---------------------------------------------------------------------- overview views
def leader_line(i, v, extra=""):
    return (f"{i}. <b>{e(v['model']['name'])}</b> · {e(short_access(v))}{extra}\n"
            f"   <i>/modelaccess {e(v['model']['id'].split('/', 1)[1])}</i>")


def render_leaderboard(board, db, cfg):
    blocks = ["<b>🏆 PERSONAL MODEL ACCESS LEADERBOARD</b>\n<i>Ranked on capability for the price you'd pay, not on "
              "how many models a provider lists.</i>"]
    sections = [("free_models", "🆓 Best completely free (hosted, no payment)"),
                ("coding", "💻 Best for coding"), ("reasoning", "🧠 Best for reasoning"), ("agents", "🤖 Best for agents"),
                ("cheapest_premium", "💎 Cheapest premium model access"), ("value", "⭐ Best overall value")]
    for key, title in sections:
        rows = board.get(key) or []
        if not rows:
            continue
        lines = []
        for i, v in enumerate(rows, 1):
            extra = premium_extra(v) if key == "cheapest_premium" else ""
            lines.append(leader_line(i, v, extra))
        blocks.append(f"<b>{title}</b>\n" + "\n".join(lines))
    prov = board.get("free_providers") or []
    if prov:
        blocks.append("<b>🔌 Best free API providers</b>\n" + "\n".join(
            f"{i}. <b>{e(r['name'])}</b> · best free: {e(', '.join(v['model']['name'] for v in r['models']))}"
            + (f" · {e((r['terms'] or {}).get('daily_limit') or (r['terms'] or {}).get('rate_limit'))}"
               if (r["terms"] or {}).get("daily_limit") or (r["terms"] or {}).get("rate_limit") else "")
            + f" · {e(VERIFICATION.get(r['verification'], ''))}" for i, r in enumerate(prov, 1)))
    blocks.append(checked_line(db, cfg["settings"]))
    return finish(blocks)


def platform_line(p, state):
    status = state.get("shown_status") or p.get("status")
    when = state.get("verified")
    label = status_text(status)
    if status != "UNVERIFIED":
        label += f" · ✅ verified {msg.human_date(when)}" if when else " · ⚠️ UNVERIFIED"
    text = p.get("limits") or p.get("claim") or ""
    if state.get("check") == "claim_not_found":
        text += " (the claim is no longer on its source page)"
    return (f"• <b>{e(p['name'])}</b> · {e(label)}\n"
            f"  {e(msg.clip(text, 220))}" + (f" · <i>models: {e(p['models'])}</i>" if p.get("models") else "")
            + f"\n  🔗 {msg.link(p['url'].replace('https://', '')[:50], p['url'])}")


def render_free(db, views, cfg, n=8):
    blocks = ["<b>🆓 FREE MODEL ACCESS</b>\n<i>Every free tier here has limits (requests, tokens or credits). "
              "Confirmed offers first, then claims that still need checking.</i>"]
    by_provider = {}
    for v in views.values():
        for o in v["offers"]:
            if confirmed(o) and o["status"] in ("LIMITED_FREE", "FREE_CREDITS"):
                by_provider.setdefault(o["provider"], []).append((v, o))
    groups = [("official_free_tier", "🏛 Official free tiers"), ("third_party_free_api", "🔌 Third-party free APIs"),
              ("coding_platform", "💻 Coding platforms")]
    for access, title in groups:
        lines = []
        for pid, rows in sorted(by_provider.items(), key=lambda kv: -max(v["capability"] for v, _ in kv[1])):
            rows = [r for r in rows if r[1].get("access") == access]
            if not rows:
                continue
            rows.sort(key=lambda r: -r[0]["capability"])
            o = rows[0][1]
            t = o.get("terms") or {}
            limits = "; ".join(x for x in (t.get("rate_limit"), t.get("daily_limit")) if x) or "limits not stated"
            names = ", ".join(r[0]["model"]["name"] for r in rows[:n])
            more = f" (+{len(rows) - n} more)" if len(rows) > n else ""
            lines.append(f"<b>{e(o.get('provider_name'))}</b> · {e(ver_text(o))}\n  <i>{e(limits)}</i>\n"
                         f"  {e(names)}{e(more)}" + (f"\n  🔗 {msg.link('terms', o.get('url'))}" if o.get("url") else ""))
        if lines:
            blocks.append(f"<b>{title}</b>\n" + "\n".join(lines))
    plats = cfg.get("platforms") or []
    state = db.platforms
    ok = [p for p in plats if (state.get(p["id"]) or {}).get("shown_status") not in (None, "UNVERIFIED", "DISCONTINUED")]
    if ok:
        blocks.append("<b>📱 Apps and coding platforms (verified)</b>\n" + "\n".join(platform_line(p, state[p["id"]]) for p in ok))
    ow = sorted((v for v in views.values() if v["model"].get("open_weights")), key=lambda v: -v["capability"])
    if ow:
        blocks.append(f"<b>🖥 Open weights (free to self-host, your hardware)</b>\n{len(ow)} models. Strongest: "
                      + e(", ".join(v["model"]["name"] for v in ow[:6])))
    unv = [p for p in plats if (state.get(p["id"]) or {}).get("shown_status", p.get("status")) == "UNVERIFIED"]
    leads = sorted(db.leads.values(), key=lambda x: x.get("first_seen") or "", reverse=True)[:4]
    if unv or leads:
        lines = [platform_line(p, state.get(p["id"]) or {}) for p in unv]
        lines += [f"• {msg.link(msg.clip(x['title'], 120), x['url'])} <i>({e(x.get('source', 'community'))}, "
                  f"{e(x.get('confirmed_note') or 'not confirmed')})</i>" for x in leads]
        blocks.append("<b>⚠️ UNVERIFIED claims and community leads</b>\n<i>Shown so you can check them; not confirmed by "
                      "any official page or catalog.</i>\n" + "\n".join(lines))
    gone = [p for p in plats if (state.get(p["id"]) or {}).get("shown_status") == "DISCONTINUED"]
    if gone:
        blocks.append("<b>⛔ Discontinued</b>\n" + "\n".join(platform_line(p, state[p["id"]]) for p in gone))
    blocks.append(checked_line(db, cfg["settings"]))
    return finish(blocks)


def render_cheapest(db, views, by_cat, board, cfg):
    blocks = ["<b>💸 CHEAPEST MODEL ACCESS</b>\n<i>Confirmed per-token prices (USD per 1M tokens, blended 3:1). "
              "Each category shows the cheapest of its stronger half, so tiny models don't crowd it out; "
              "subscriptions are listed separately in each model's comparison.</i>"]
    for cat, d in by_cat.items():
        lines = [f"<b>{CATEGORY_LABEL.get(cat, cat)}</b>"]
        for v in d["best"]:
            o = v["picks"]["cheapest"]
            lines.append(f"  • {e(v['model']['name'])}: {money(o['blended'])} on {e(o.get('provider_name'))}")
        blocks.append("\n".join(lines))
    prem = board.get("cheapest_premium") or []
    if prem:
        blocks.append("<b>💎 Cheapest premium model access</b>\n" + "\n".join(
            leader_line(i, v, premium_extra(v)) for i, v in enumerate(prem, 1)))
    blocks.append(checked_line(db, cfg["settings"]))
    return finish(blocks)


def premium_extra(v):
    if v.get("access_price") == 0:
        return f" · free tier (list price {money(v['ref_price'])} blended)"
    return f" · {v['saving']}% under the {money(v['ref_price'])} list price"


def change_text(ev, db):
    m = db.models.get(ev.get("model")) or {}
    name = m.get("name") or ev.get("model")
    o = db.offers.get(ev.get("offer") or "") or {}
    snap = next((x for x in (ev.get("new"), ev.get("old")) if isinstance(x, dict)), {})
    prov = o.get("provider_name") or snap.get("provider_name") or ""
    kind = ev["kind"]
    if kind == "new_offer":
        return f"{name}: new on {prov} ({price_text(ev.get('new') or o)})"
    if kind == "offer_removed":
        return f"{name}: no longer listed on {prov}"
    if kind == "changed" and ev.get("field") == "price":
        return f"{name} on {prov}: {price_text({'price': ev.get('old')})} → {price_text({'price': ev.get('new')})}"
    if kind == "changed":
        return f"{name} on {prov}: {ev.get('field')} changed"
    if kind == "model_status":
        return f"{name}: {STATUS.get(ev.get('old'), STATUS[None])[1]} → {STATUS.get(ev.get('new'), STATUS[None])[1]}"
    if kind == "new_model":
        return f"{name}: first seen in the catalogs"
    return f"{name}: {kind}"


def render_overview(db, views, board, cfg):
    s = cfg["settings"]
    counts = {}
    for v in views.values():
        counts[v["model"].get("status")] = counts.get(v["model"].get("status"), 0) + 1
    providers = len({o["provider"] for o in db.offers.values() if o.get("active", True)})
    head = (f"<b>🔑 MODEL ACCESS INTELLIGENCE</b>\n<i>Where models can be used for free or cheaply: {len(views)} "
            f"models, {providers} providers.</i>\n"
            + " · ".join(f"{status_text(k)} {counts[k]}" for k in ("FREE", "LIMITED_FREE", "FREE_CREDITS", "LOW_COST",
                                                                 "PAID_ONLY", "UNVERIFIED") if counts.get(k)))
    blocks = [head]
    top = board.get("free_models") or []
    if top:
        blocks.append("<b>🆓 Best free models right now</b>\n" + "\n".join(leader_line(i, v) for i, v in enumerate(top, 1)))
    prem = (board.get("cheapest_premium") or [])[:3]
    if prem:
        blocks.append("<b>💎 Cheapest premium access</b>\n" + "\n".join(
            leader_line(i, v, premium_extra(v)) for i, v in enumerate(prem, 1)))
    recent = [ev for ev in db.events_since(24 * 7) if ev["kind"] in ("new_offer", "offer_removed", "model_status")
              and (ev["kind"] != "new_offer" or (ev.get("new") or {}).get("status") in ("FREE", "LIMITED_FREE", "FREE_CREDITS"))]
    if recent:
        blocks.append("<b>🔄 Changes this week</b>\n" + "\n".join(f"• {e(change_text(ev, db))}" for ev in recent[-6:]))
    blocks.append("<b>How to ask</b>\n/modelaccess free · /modelaccess cheapest · /modelaccess leaderboard\n"
                  "/modelaccess Claude · /modelaccess OpenAI gpt-6.1 · /modelaccess deepseek v4 flash\n"
                  "/modelaccess compare kimi k3 · /modelaccess alternatives claude fable 5.2")
    blocks.append(checked_line(db, s))
    return finish(blocks)


def render_not_found(query, db, cfg):
    n = len(db.models)
    return finish([f"I couldn't find <b>{e(query)}</b> among the {n} models in the access database. Try the model id "
                   "(e.g. <i>/modelaccess deepseek-v4-flash</i>) or a company (<i>/modelaccess Claude</i>).",
                   checked_line(db, cfg["settings"])])


def render_empty():
    return finish(["The model access database hasn't been built yet. It refreshes with the 6-hourly news run; "
                   "you can also start it from GitHub: Actions → AI news digest → Run workflow → model-access."])


def render_help():
    return finish(["<b>/modelaccess</b>: where AI models can be used for free or cheaply.\n"
                   "/modelaccess · overview\n/modelaccess free · confirmed free access, then unverified claims\n"
                   "/modelaccess cheapest · cheapest confirmed access per category\n"
                   "/modelaccess leaderboard · your personal access leaderboard\n"
                   "/modelaccess Claude · a company's models (also OpenAI, Gemini, Grok, Qwen, Kimi, GLM, DeepSeek…)\n"
                   "/modelaccess Claude fable 5.2 · one model\n/modelaccess compare [model] · every provider's price\n"
                   "/modelaccess alternatives [model] · similar models that are free or cheaper"])
