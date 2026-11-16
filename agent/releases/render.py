"""Telegram messages for model releases (HTML, mobile-friendly: grouped sections instead of wide tables)."""

import html
import re
from datetime import date

from ..digest import split_messages
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
    blocks = [
        "🚨 <b>NEW AI MODEL RELEASE</b>",
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
    ideas = ai.get("content_ideas") or {}
    name, label = facts["model_name"], c["label"]
    prev_name = prev["name"] if prev else f"the previous {c['product']} model"
    news = ideas.get("news_video") or f"{label} releases {name}: what is new and who it is for"
    explainer = ideas.get("explainer") or f"How {name} works and when to use it"
    versus = ideas.get("comparison_video") or f"{name} vs {prev_name}: what actually changed"
    blocks.append("<b>CONTENT OPPORTUNITIES:</b>\n"
                  f"1. News video: {e(news)}\n2. Educational explainer: {e(explainer)}\n"
                  f"3. Model comparison video: {e(versus)}")
    verif = VERIFICATION_LABEL[facts["verification"]]
    reasons = "; ".join(facts["reasons"][:3])
    blocks.append(f"<b>VERIFICATION:</b>\n{verif}\n<i>{e(reasons)}</i>"
                  + (f"\n<i>AI reading by {e(facts['ai_model'])}; {facts['ai_dropped']} unsupported AI statements "
                     f"discarded</i>" if facts["ai_model"] else "\n<i>No AI reading this run: facts above come "
                                                                 "straight from the official pages.</i>"))
    blocks.append(f"<b>DELIVERY:</b>\n{PENDING_LINE}")
    return split_messages(blocks)


def render_review(facts, companies):
    c = companies[facts["company"]]
    links = "\n".join(official_links(facts))
    blocks = [
        "🔎 <b>POSSIBLE NEW MODEL: REQUIRES MANUAL REVIEW</b>",
        f"<b>COMPANY:</b> {e(c['label'])} ({e(c['product'])})",
        f"<b>MODEL:</b> {e(facts['model_name'])}\n{_code_ids(facts['model_ids'])}",
        "<b>WHY IT WAS NOT CONFIRMED:</b>\n" + "\n".join(f"• {e(r)}" for r in facts["reasons"][:4]),
        f"<b>OFFICIAL SOURCES:</b>\n{links}",
        "<b>VERIFICATION:</b>\nREQUIRES MANUAL REVIEW\n<i>Open the sources before posting about it.</i>",
        f"<b>DELIVERY:</b>\n{PENDING_LINE}",
    ]
    return split_messages(blocks)


HEADERS = {
    "MAJOR_MODEL_UPGRADE": "⬆️ <b>MAJOR MODEL UPGRADE</b>",
    "MODEL_AVAILABILITY": "🟢 <b>MODEL AVAILABILITY</b>",
    "DOCUMENTATION_OR_PRICING_CHANGE": "📄 <b>DOCUMENTATION OR PRICING CHANGE</b>",
    "PRODUCT_OR_FEATURE_ANNOUNCEMENT": "🧩 <b>PRODUCT OR FEATURE ANNOUNCEMENT</b>",
    "UNCONFIRMED_EARLY_SIGNAL": "👀 <b>UNCONFIRMED EARLY SIGNAL</b>",
}


def render_update(facts, companies):
    c = companies[facts["company"]]
    sources = "\n".join(f"• {_link(s['name'], s['url'])}" for s in facts["sources"])
    blocks = [
        HEADERS.get(facts["category"], "🔔 <b>MODEL UPDATE</b>"),
        f"<b>COMPANY:</b> {e(c['label'])} ({e(c['product'])})",
        f"<b>MODEL:</b> {e(facts['model_name'])}\n{_code_ids(facts['model_ids'])}",
        f"<b>WHAT CHANGED:</b>\n{e(facts['title'])}" + (f"\n{e(facts['text'])}" if facts["text"] and
                                                        facts["text"] != facts["title"] else ""),
        f"<b>DATE:</b> {e(fmt_date(facts['date']))}",
        f"<b>SOURCES:</b>\n{sources}",
        f"<b>VERIFICATION:</b>\n{VERIFICATION_LABEL[facts['verification']]}\n<i>{e('; '.join(facts['reasons'][:2]))}</i>",
    ]
    if facts["category"] == "UNCONFIRMED_EARLY_SIGNAL":
        blocks.append("<i>Third-party listing only. Not an official release until the company confirms it.</i>")
    blocks.append(f"<b>DELIVERY:</b>\n{PENDING_LINE}")
    return split_messages(blocks)


def render_followup(rec, note, sources, companies):
    """A meaningful update to a release that was already announced (e.g. the blog post arrives after the docs)."""
    c = companies[rec["company"]]
    blocks = [
        f"🔄 <b>UPDATE: {e(rec['model_name'])}</b>",
        f"<b>COMPANY:</b> {e(c['label'])} ({e(c['product'])})",
        f"<b>NEW INFORMATION:</b>\n{e(note)}",
        "<b>SOURCES:</b>\n" + "\n".join(f"• {_link(s['name'], s['url'])}" for s in sources),
        "<b>VERIFICATION:</b>\nOFFICIAL UPDATE",
        f"<b>DELIVERY:</b>\n{PENDING_LINE}",
    ]
    return split_messages(blocks)


def delivered_line(message_ids, sent_at):
    return f"✅ SENT. Telegram confirmed message #{message_ids[-1]} at {sent_at[11:16]} UTC."


def render_receipt(notification, message_ids, sent_at):
    return (f"✅ <b>Delivery receipt</b>\n{e(notification['model_name'])}: Telegram confirmed "
            f"{'message' if len(message_ids) == 1 else 'messages'} "
            f"{', '.join('#' + str(m) for m in message_ids)} at {e(sent_at[11:16])} UTC.\n"
            f"Verification: {VERIFICATION_LABEL.get(notification['verification'], notification['verification'])}")


def render_health(source_name, company_label, count, error):
    return [f"⚠️ <b>Release monitor</b>\n<b>{e(source_name)}</b> ({e(company_label)}) has failed {count} runs in a "
            f"row: <i>{e(error[:200])}</i>\nNew {e(company_label)} models may be missed until it is fixed in "
            f"releases.yaml. Other sources keep working."]


def render_recent(records, companies):
    """The /releases command: the latest releases with their verification and delivery status."""
    if not records:
        return ["No model releases recorded yet. The release monitor checks the official sources every run."]
    lines = ["<b>🧠 Latest model releases</b>"]
    for r in records:
        label = companies.get(r["company"], {}).get("label", r["company"])
        d = r.get("delivery", {})
        lines.append(f"• <b>{e(r['model_name'])}</b> ({e(label)}) · {e(CATEGORY_LABEL.get(r['classification'], r['classification']))}"
                     f"\n  {e(fmt_date(r.get('announcement_date')))} · "
                     f"{VERIFICATION_LABEL.get(r['verification'], r['verification'])} · delivery {e(d.get('status', '?'))}"
                     + (f"\n  {_link('source', r['source_url'])}" if r.get("source_url") else ""))
    return split_messages(lines)


def plain(messages):
    return "\n\n".join(re.sub(r"<[^>]+>", "", html.unescape(m)) for m in messages)
