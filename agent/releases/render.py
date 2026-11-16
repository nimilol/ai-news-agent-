"""Telegram messages for model releases (HTML, mobile-friendly: grouped sections instead of wide tables).

The full release alert keeps the Phase 1 layout and adds the standard section/priority line, a plain-language
"Explained" block and A/B/C content opportunities (agent/messages.py). The shorter alerts are standard cards."""

import html
import re
from datetime import date

from .. import messages as msg
from .analyze import COMPARE_ROWS
from .classify import CATEGORY_LABEL, VERIFICATION_LABEL

e = html.escape
PENDING_LINE = "Pending Telegram confirmation."


def fmt_date(value):
    if not value:
        return "Not stated in the official sources"
    try:
        if len(value) == 7:
            return date.fromisoformat(value + "-01").strftime("%B %Y") + " (day not stated)"
        return date.fromisoformat(value[:10]).strftime("%-d %B %Y")
    except ValueError:
        return value


def _link(name, url):
    return f'<a href="{e(url, quote=True)}">{e(name)}</a>' if url else e(name)


def _code_ids(ids):
    return ", ".join(f"<code>{e(m)}</code>" for m in ids)


def _ticks_to_code(text):
    return re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", e(text))


def official_links(facts):
    """Original announcement / official documentation / model or API page, from the release's own sources."""
    ann = next((t for t in facts["texts"] if t["kind"] == "announcement" and t.get("url")), None)
    doc = next((t for t in facts["texts"] if t["kind"] == "changelog" and t.get("url")), None)
    page = next((o for o in facts["official"] if o["event"] in ("listed", "repo_created") and o.get("url")), None)
    lines = [f"• Original announcement: {_link(ann['source'], ann['url'])}" if ann
             else "• Original announcement: not published yet (the release was found in the official docs)"]
    lines.append(f"• Official documentation: {_link(doc['source'], doc['url'])}" if doc
                 else "• Official documentation: no release note yet")
    lines.append(f"• Model information / API page: {_link(page['source'], page['url'])}" if page
                 else "• Model information / API page: not listed yet")
    return lines


def _cell(value):
    return e(value) if value else "<i>not established</i>"


def render_release(facts, companies):
    """The full 🚨 NEW AI MODEL RELEASE alert. Returns a list of Telegram messages."""
    c = companies[facts["company"]]
    ai = facts["ai"]
    prev = facts["previous"]
    main_id = facts["model_ids"][0]
    priority = facts.get("priority", "P1")
    blocks = [
        "🚨 <b>NEW AI MODEL RELEASE</b>\n"
        f"<i>{msg.SECTIONS['new_models'][0]} {msg.SECTIONS['new_models'][1]} · {msg.priority_label(priority)}"
        f"{' · flagship' if facts.get('flagship') else ''}</i>",
        f"<b>COMPANY:</b>\n{e(c['label'])} ({e(c['product'])})",
        f"<b>NEW MODEL:</b>\n{e(facts['model_name'])}\n{_code_ids(facts['model_ids'])}",
        "<b>RELEASE INFORMATION:</b>\n"
        f"• Announcement date: {e(fmt_date(facts['announcement_date']))}\n"
        f"• Availability date: {e(fmt_date(facts['availability_date']))}\n"
        f"• Model category: {e(CATEGORY_LABEL[facts['category']])}\n"
        f"• Availability status: {e(facts['availability_status'])}\n"
        f"• API access: {_ticks_to_code(facts['api_access'])}",
    ]
    if facts["whats_new"]:
        blocks.append("<b>WHAT'S NEW:</b>\n" + "\n".join(f"• {e(x)}" for x in facts["whats_new"]))
    else:
        blocks.append("<b>WHAT'S NEW:</b>\n<i>The official sources did not describe the changes in readable text.</i>")
    explained = msg.explain_block(facts.get("explain"))
    if explained:
        blocks.append(explained)

    comp = ["<b>COMPARISON WITH PREVIOUS MODEL:</b>"]
    if prev:
        comp.append(f"Previous model: {e(prev['name'])}" + (f" (<code>{e(prev['id'])}</code>)" if prev.get("id") else ""))
        comp.append(f"New model: {e(facts['model_name'])} (<code>{e(main_id)}</code>)")
        comp.append(f"<i>How the previous model was chosen: {e(prev['method'])}"
                    + (f' — "{e(prev["evidence"])}"' if prev.get("evidence") else "") + "</i>")
    else:
        comp.append("Previous model: <i>not established</i> (no earlier model in the same family was found in the "
                    "official sources)")
    blocks.append("\n".join(comp))
    missing = []
    for key, label in COMPARE_ROWS:
        cell = facts["comparison"].get(key)
        if not cell:
            missing.append(label.split(" ", 1)[1])
            continue
        basis = f" <i>({e(cell['basis'])})</i>" if cell.get("basis") else ""
        blocks.append(f"<b>{e(label)}</b>{basis}\n• Previous: {_cell(cell['previous'])}\n• New: {_cell(cell['new'])}")

    bench = ai.get("benchmarks") or []
    lines = ["<b>📈 Benchmarks</b>"]
    if bench:
        lines.append("<i>Company-reported (from the official announcement):</i>")
        for b in bench:
            prev_score = f" vs {e(str(b['previous']))} before" if b.get("previous") else ""
            lines.append(f"• {e(str(b.get('name', '')))}: {e(str(b.get('new', '')))}{prev_score}")
    else:
        lines.append("• Company-reported: none found in the official sources")
    lines.append("• Independent: none collected (the agent only reads official sources)")
    blocks.append("\n".join(lines))
    if ai.get("claims"):
        blocks.append("<b>💬 Qualitative claims by the company</b>\n" +
                      "\n".join(f'• {e(x["text"])}' for x in ai["claims"][:4]))

    improved = [f"• {e(x)} <i>(official specs)</i>" for x in facts["improved"]]
    improved += [f"• {e(x)} <i>(official specs, worse)</i>" for x in facts["regressed"]]
    improved += [f"• {e(b['name'])}: {e(str(b['new']))} vs {e(str(b['previous']))} <i>(company-reported)</i>"
                 for b in bench if b.get("previous")]
    improved += [f"• {e(x)} <i>(interpretation by {e(facts['ai_model'] or 'AI')}, from the sources above)</i>"
                 for x in ai.get("interpretation", [])]
    blocks.append("<b>WHAT ACTUALLY IMPROVED:</b>\n" + ("\n".join(improved) if improved else
                  "• Nothing measurable could be established from the official sources yet."))

    gaps = []
    if not prev:
        gaps.append("Which model this replaces")
    if missing:
        gaps.append("No evidence yet for: " + ", ".join(missing))
    if not bench:
        gaps.append("Benchmark scores (none in the official sources)")
    gaps.append("Independent benchmark results")
    if not facts["availability_date"]:
        gaps.append("The availability date")
    if facts["unchanged"]:
        gaps.append("Unchanged: " + "; ".join(facts["unchanged"]))
    blocks.append("<b>WHAT HAS NOT BEEN ESTABLISHED:</b>\n" + "\n".join(f"• {e(x)}" for x in gaps))

    blocks.append("<b>OFFICIAL SOURCES:</b>\n" + "\n".join(official_links(facts)))
    why = ai.get("why_it_matters") or (
        f"{c['label']} has a new {c['product']} model. Builders should check whether it changes which model they "
        f"use for their tasks and costs; for teaching, it is a chance to show how {c['product']} models evolve.")
    blocks.append(f"<b>WHY THIS MATTERS:</b>\n{e(why)}")
    blocks.append(msg.content_block(ai.get("content") or fallback_content(facts, c), facts["model_name"]))
    verif = VERIFICATION_LABEL[facts["verification"]]
    reasons = "; ".join(facts["reasons"][:3])
    blocks.append(f"<b>VERIFICATION:</b>\n{verif}\n<i>{e(reasons)}</i>"
                  + (f"\n<i>AI reading by {e(facts['ai_model'])}; {facts['ai_dropped']} unsupported AI statements "
                     f"discarded</i>" if facts["ai_model"] else "\n<i>No AI reading this run: facts above come "
                                                                 "straight from the official pages.</i>"))
    blocks.append(f"<b>DELIVERY:</b>\n{PENDING_LINE}")
    return msg.paginate([(b, None) for b in blocks])


def fallback_content(facts, c):
    """A/B/C ideas without AI: honest starting points that name what still has to be checked."""
    name = facts["model_name"]
    prev = facts["previous"]["name"] if facts.get("previous") else f"the previous {c['product']} model"
    return [
        {"kind": "news", "title": f"{c['label']} releases {name}: what's new",
         "angle": f"What changed compared with {prev}, who can use it and where.",
         "why": f"A new {c['product']} model changes what builders and viewers can do.",
         "research": "Read the full announcement; confirm pricing, limits and regional availability."},
        {"kind": "education", "title": f"How {name} works, explained",
         "angle": "The capabilities and limits behind the announcement, in plain language.",
         "why": "Viewers want to know when to use it, not just that it exists.",
         "research": "Check the model card or system card; architecture details are often not disclosed."},
        {"kind": "analysis", "title": f"{name} vs {prev}: a hands-on test",
         "angle": "Test the company's claims on the same tasks with both models.",
         "why": "Company benchmarks are self-reported; a fair test is real value for viewers.",
         "research": "Run identical prompts on both; compare quality, speed and cost; look for independent benchmarks."},
    ]


def _standard(heading, card, verification_note=None):
    """A standard card plus the release monitor's VERIFICATION and DELIVERY lines."""
    blocks = [heading, msg.render_card(card)]
    if verification_note:
        blocks.append(f"<i>{e(verification_note)}</i>")
    blocks.append(f"<b>DELIVERY:</b> {PENDING_LINE}")
    return msg.paginate([("\n".join(blocks), None)])


def _heading(section, title=None):
    emoji, name = msg.SECTIONS[section]
    return f"<b>{emoji} {title or name}</b>"


def render_review(facts, companies):
    c = companies[facts["company"]]
    links = [(t["source"], t["url"]) for t in facts["texts"] if t.get("url")]
    card = msg.card("new_models", "P1", f"Possible new model: {facts['model_name']}", company=c["label"],
                    what=f"An official {c['label']} source points to {facts['model_name']} "
                         f"({', '.join(facts['model_ids'])}), but it could not be verified automatically.",
                    why="It may be a new model. Check the sources before posting about it.",
                    details=facts["reasons"][:3], sources=links[:4],
                    date=facts.get("announcement_date"), verification=VERIFICATION_LABEL[facts["verification"]])
    return _standard(_heading("new_models", "POSSIBLE NEW MODEL: REQUIRES MANUAL REVIEW"), card)


UPDATE_SECTION = {"MAJOR_MODEL_UPGRADE": "model_updates", "MODEL_AVAILABILITY": "model_updates",
                  "DOCUMENTATION_OR_PRICING_CHANGE": "model_updates", "PRODUCT_OR_FEATURE_ANNOUNCEMENT": "industry",
                  "UNCONFIRMED_EARLY_SIGNAL": "industry"}
UPDATE_TITLE = {"MAJOR_MODEL_UPGRADE": "MAJOR MODEL UPGRADE", "MODEL_AVAILABILITY": "MODEL AVAILABILITY",
                "DOCUMENTATION_OR_PRICING_CHANGE": "DOCUMENTATION OR PRICING CHANGE",
                "PRODUCT_OR_FEATURE_ANNOUNCEMENT": "PRODUCT OR FEATURE ANNOUNCEMENT",
                "UNCONFIRMED_EARLY_SIGNAL": "UNCONFIRMED EARLY SIGNAL"}
UPDATE_WHY = {
    "MAJOR_MODEL_UPGRADE": "A new version of a model people already use: behaviour, quality or price may change.",
    "MODEL_AVAILABILITY": "More people can use this model now (a new API, app, region or tier).",
    "DOCUMENTATION_OR_PRICING_CHANGE": "Prices, limits or retirement dates affect what builders pay and plan for.",
    "PRODUCT_OR_FEATURE_ANNOUNCEMENT": "A product change around an existing model.",
    "UNCONFIRMED_EARLY_SIGNAL": "A third-party listing; models often appear here before the official announcement. "
                                "Not confirmed by the company.",
}


def render_update(facts, companies):
    c = companies[facts["company"]]
    cat = facts["category"]
    card = msg.card(UPDATE_SECTION.get(cat, "model_updates"), facts.get("priority", "P2"),
                    f"{facts['model_name']}: {facts['title']}" if facts["title"] else facts["model_name"],
                    company=c["label"], what=facts["text"] or facts["title"], why=UPDATE_WHY.get(cat),
                    details=[", ".join(facts["model_ids"])], date=facts.get("date"),
                    sources=[(s["name"], s["url"]) for s in facts["sources"]],
                    verification=VERIFICATION_LABEL[facts["verification"]])
    emoji = msg.SECTIONS[UPDATE_SECTION.get(cat, "model_updates")][0]
    return _standard(f"<b>{emoji} {UPDATE_TITLE.get(cat, 'MODEL UPDATE')}</b>", card,
                     "; ".join(facts["reasons"][:2]))


def render_followup(rec, note, sources, companies):
    """A meaningful update to a release that was already announced (e.g. the blog post arrives after the docs)."""
    c = companies[rec["company"]]
    card = msg.card("new_models", "P1", rec["model_name"], company=c["label"], update=note,
                    what=f"New official information about {rec['model_name']}, announced earlier.",
                    why="It completes or confirms the release alert you already received.",
                    sources=[(s["name"], s["url"]) for s in sources], verification="OFFICIAL UPDATE",
                    date=rec.get("announcement_date"))
    return _standard(_heading("new_models", f"UPDATE: {e(rec['model_name'])}"), card)


def delivered_line(message_ids, sent_at):
    return f"✅ SENT. Telegram confirmed message #{message_ids[-1]} at {sent_at[11:16]} UTC."


def render_receipt(notification, message_ids, sent_at):
    return (f"✅ <b>Delivery receipt</b>\n{e(notification['model_name'])}: Telegram confirmed "
            f"{'message' if len(message_ids) == 1 else 'messages'} "
            f"{', '.join('#' + str(m) for m in message_ids)} at {e(sent_at[11:16])} UTC.\n"
            f"Verification: {VERIFICATION_LABEL.get(notification['verification'], notification['verification'])}")


def render_health(source_name, company_label, count, error):
    card = msg.card("health", "P1", f"Release monitor: {source_name} is failing", company=company_label,
                    what=f"It has failed {count} runs in a row: {error[:200]}",
                    why=f"New {company_label} models may be missed until it is fixed in releases.yaml. "
                        "Other sources keep working.", verification="System notification")
    return [_heading("health") + "\n" + msg.render_card(card)]


def render_recent(records, companies, title="🧠 Model release history"):
    """/releases: the latest releases with category, priority, verification and delivery status."""
    if not records:
        return ["No model releases recorded yet. The release monitor checks the official sources every run."]
    blocks = [f"<b>{e(title)}</b>"]
    for r in records:
        label = companies.get(r["company"], {}).get("label", r["company"])
        d = r.get("delivery", {})
        prev = (r.get("previous_model") or {}).get("name")
        lines = [f"<b>{e(r['model_name'])}</b> · {e(label)}",
                 f"<i>{e(CATEGORY_LABEL.get(r['classification'], r['classification']))} · "
                 f"{VERIFICATION_LABEL.get(r['verification'], r['verification'])} · 📅 "
                 f"{e(fmt_date(r.get('announcement_date')))}</i>"]
        if prev:
            lines.append(f"↔️ Previous: {e(prev)}" + (f" · {e(r['comparison_summary'])}" if r.get("comparison_summary") else ""))
        lines.append(f"📬 Delivery: {e(d.get('status', '?'))}" + (f" (#{d['message_ids'][-1]})" if d.get("message_ids") else "")
                     + (f" · {_link('source', r['source_url'])}" if r.get("source_url") else ""))
        blocks.append("\n".join(lines))
    return msg.paginate([(b, None) for b in blocks])


def render_models(records, companies):
    """/models: confirmed new model releases only."""
    confirmed = [r for r in records if r.get("classification") == "NEW_MODEL_RELEASE"
                 and r.get("verification") == "OFFICIAL_CONFIRMED"]
    if not confirmed:
        return ["No confirmed model releases recorded yet."]
    return render_recent(confirmed, companies, title="🚨 Recent confirmed model releases")


def render_compare(a, b, companies):
    """/compare: two models side by side from the official catalogs (specs only: no benchmarks, no AI)."""
    from .analyze import spec_cells, spec_diffs

    rows = [("context", "📏 Context window"), ("output", "📤 Output limit"), ("pricing", "💰 API pricing"),
            ("speed", "⚡ Speed"), ("multimodal", "🖼 Inputs and outputs"), ("reasoning", "🧠 Reasoning"),
            ("availability", "🌍 Availability")]
    ca, cb = spec_cells(a["specs"], a.get("status")), spec_cells(b["specs"], b.get("status"))
    lab = lambda m: companies.get(m["company"], {}).get("label", m["company"])  # noqa: E731
    lines = [f"<b>⚖️ {e(a['name'])} vs {e(b['name'])}</b>",
             f"<i>{e(lab(a))} · <code>{e(a['id'])}</code> vs {e(lab(b))} · <code>{e(b['id'])}</code></i>"]
    for key, label in rows:
        if ca.get(key) or cb.get(key):
            lines.append(f"<b>{label}</b>\n• {e(a['name'])}: {_cell(ca.get(key))}\n• {e(b['name'])}: {_cell(cb.get(key))}")
    better, worse, same = spec_diffs(a["specs"], b["specs"])
    if better or worse:
        lines.append(f"<b>From {e(a['name'])} to {e(b['name'])}</b>\n" +
                     "\n".join(f"• {e(x)}" for x in better + worse))
    for m in (a, b):
        if m["specs"].get("description"):
            lines.append(f"<i>{e(m['name'])}: {e(msg.clip(m['specs']['description'], 220))}</i>")
    lines.append("<i>Official catalog specs only. Benchmarks and quality are not compared here.</i>")
    return msg.paginate([(x, None) for x in lines])


def render_sources(cfg, failures):
    """/sources: every official source the release monitor watches, with its health."""
    blocks = ["<b>📡 Monitored official sources</b>"]
    for key, c in cfg["companies"].items():
        lines = [f"<b>{e(c['label'])} ({e(c['product'])})</b>"]
        for s in c["sources"]:
            n = failures.get(s["name"], 0)
            mark = "✅" if not n else f"⚠️ failing {n}×"
            where = s.get("url") or f"{s['type'].split('_')[0]}: {s.get('org')}"
            lines.append(f"{mark} {_link(s['name'], s.get('url'))} <i>({e(s['role'])})</i>"
                         + ("" if s.get("url") else f" · {e(where)}"))
        blocks.append("\n".join(lines))
    early = ", ".join(e(s["name"]) for s in cfg["early_signals"])
    blocks.append(f"<i>Early signals only (never proof of a release): {early}</i>")
    return msg.paginate([(b, None) for b in blocks])


def plain(messages):
    return "\n\n".join(re.sub(r"<[^>]+>", "", html.unescape(m)) for m in messages)
