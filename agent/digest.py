"""Turns collected items into a briefing: the AI prompt, reading the AI's answer, and rendering
the result for Telegram (HTML), email (HTML + text) and the Markdown archive."""

import html
import json
import re

from .common import TIER_RANK, clean_text, parse_date

SYSTEM_PROMPT = """You are the research assistant of an AI news creator who covers four AI companies:
Anthropic (Claude), OpenAI (GPT, ChatGPT), xAI (Grok) and Zhipu / Z.ai (GLM).
You receive a numbered list of news items collected over the last hours from official company sites,
leak trackers, communities, newsletters and the press.

Answer with JSON only, in exactly this shape:
{
  "headline": "the single most important development, one sentence ('Quiet period' if nothing notable)",
  "stories": [
    {
      "company": "anthropic | openai | xai | zhipu | industry",
      "title": "short factual headline, at most 12 words",
      "summary": "2-3 sentences: what changed, who it affects, why it matters. Keep concrete names, numbers, dates.",
      "status": "official | report | rumor",
      "importance": 1,
      "items": [1, 2]
    }
  ],
  "post_ideas": ["up to 3 short post hooks for the creator, based on the most newsworthy stories"]
}

Rules:
- Merge items about the same news into one story and list all of their numbers in "items".
- Use only facts found in the items. Never invent details, numbers, quotes or links.
- status: "official" when an item comes from the company's own official source (tier official);
  "report" when it comes from the press or newsletters; "rumor" for leaks, stealth models,
  unconfirmed claims and community speculation.
- importance: 5 = new model, major launch or shutdown, pricing/policy change affecting many users;
  4 = notable feature or model update, big deal, funding, outage; 3 = smaller update or interesting report;
  2 = minor; 1 = noise.
- Leave out items that are not really about these companies, and noise (memes, help requests, job posts,
  routine SDK patch releases). Returning few stories is fine.
- "industry" is for stories involving several of the companies, or the AI field as a whole.
- Sort stories by importance, highest first. At most 25 stories."""

STATUS_LABEL = {"official": "✅ Official", "report": "📰 Report", "rumor": "👀 Rumor"}
IMPORTANCE_MARK = {5: "🔥", 4: "⭐"}


def rank_items(items):
    return sorted(items, key=lambda i: (TIER_RANK.get(i["tier"], 9), -(_ts(i["published"]))))


def _ts(iso):
    d = parse_date(iso)
    return d.timestamp() if d else 9e12  # undated items are brand-new by definition


def build_user_message(items, now, summary_chars=400):
    lines = [f"Current time: {now:%Y-%m-%d %H:%M} UTC. {len(items)} items:\n"]
    for n, it in enumerate(items, 1):
        date = (it["published"] or "")[:10] or "new"
        companies = ",".join(it["companies"]) or "-"
        lines.append(f"[{n}] ({it['tier']} · {it['source']} · {companies} · {date}) {it['title']}")
        if it["summary"]:
            lines.append(f"    {clean_text(it['summary'], summary_chars)}")
    return "\n".join(lines)


def parse_answer(text, items, companies):
    """Read the AI's JSON. Anything unusable raises ValueError so the caller can fall back."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in the answer")
    data = json.loads(text[start : end + 1])
    stories = []
    for s in data.get("stories") or []:
        if not isinstance(s, dict):
            continue
        refs = []
        for n in s.get("items") or []:
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(items) and n not in refs:
                refs.append(n)
        if not refs:  # a story must point at real items, so nothing made-up gets through
            continue
        company = str(s.get("company", "")).lower().strip()
        try:
            importance = max(1, min(5, int(s.get("importance", 2))))
        except (TypeError, ValueError):
            importance = 2
        stories.append({
            "company": company if company in companies else "industry",
            "title": clean_text(str(s.get("title", "")), 200) or items[refs[0] - 1]["title"],
            "summary": clean_text(str(s.get("summary", "")), 900),
            "status": s.get("status") if s.get("status") in STATUS_LABEL else "report",
            "importance": importance,
            "items": refs,
        })
    if not stories and items:
        raise ValueError("the answer had no usable stories")
    return {
        "headline": clean_text(str(data.get("headline", "")), 300),
        "stories": stories,
        "post_ideas": [clean_text(str(p), 300) for p in (data.get("post_ideas") or [])[:3] if p],
    }


def fallback_brief(items, companies, per_company=10):
    """No AI available: list the headlines, grouped by company."""
    stories, counts = [], {}
    for n, it in enumerate(items, 1):
        company = it["companies"][0] if it["companies"] else "industry"
        if counts.get(company, 0) >= per_company:
            continue
        counts[company] = counts.get(company, 0) + 1
        tier = it["tier"]
        stories.append({
            "company": company if company in companies else "industry",
            "title": it["title"],
            "summary": clean_text(it["summary"], 220),
            "status": "official" if tier == "official" else "rumor" if tier in ("leak", "community") else "report",
            "importance": 4 if tier == "official" else 3 if tier == "leak" else 2,
            "items": [n],
        })
    return {"headline": "AI summary unavailable this run, so here are the raw headlines.", "stories": stories,
            "post_ideas": []}


def _story_links(story, items, max_links=3):
    links, seen = [], set()
    for n in story["items"]:
        it = items[n - 1]
        if it["url"] and it["url"] not in seen:
            seen.add(it["url"])
            links.append((it["source"], it["url"]))
    return links[:max_links]


def _sections(brief, companies):
    order = list(companies) + ["industry"]
    for key in order:
        group = [s for s in brief["stories"] if s["company"] == key]
        if group:
            group.sort(key=lambda s: -s["importance"])
            label = companies[key]["label"] if key in companies else "Across the industry"
            yield label, group


def render_telegram(brief, items, companies, meta, limit=3900):
    """Telegram HTML, split into messages under Telegram's 4096-character limit."""
    e = html.escape
    blocks = [f"<b>🛰 AI News Brief</b> · {e(meta['when'])}\n<i>{e(brief['headline'])}</i>"]
    for label, group in _sections(brief, companies):
        blocks.append(f"\n<b>━━ {e(label)} ━━</b>")
        for s in group:
            mark = IMPORTANCE_MARK.get(s["importance"], "•")
            text = f"{mark} <b>{e(s['title'])}</b>  <i>{STATUS_LABEL[s['status']]}</i>"
            if s["summary"]:
                text += f"\n{e(s['summary'])}"
            links = _story_links(s, items)
            if links:
                text += "\n↳ " + " · ".join(f'<a href="{e(u, quote=True)}">{e(name)}</a>' for name, u in links)
            blocks.append(text)
    if brief["post_ideas"]:
        blocks.append("\n<b>💡 Post ideas</b>\n" + "\n".join(f"• {e(p)}" for p in brief["post_ideas"]))
    blocks.append(f"\n<i>{e(meta['footer'])}</i>")
    if meta.get("warning"):
        blocks.append(f"<i>⚠️ {e(meta['warning'])}</i>")

    messages, current = [], ""
    for b in blocks:
        if len(b) > limit:
            b = b[: limit - 1] + "…"
        if current and len(current) + len(b) + 1 > limit:
            messages.append(current)
            current = ""
        current = f"{current}\n{b}" if current else b
    if current:
        messages.append(current)
    return messages


def render_email_html(brief, items, companies, meta):
    e = html.escape
    parts = [
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:680px;margin:auto;color:#1a1a1a;line-height:1.5">',
        f'<h2 style="margin-bottom:4px">AI News Brief</h2><div style="color:#666">{e(meta["when"])}</div>',
        f'<p style="font-size:17px"><b>{e(brief["headline"])}</b></p>',
    ]
    for label, group in _sections(brief, companies):
        parts.append(f'<h3 style="border-bottom:2px solid #ddd;padding-bottom:4px;margin-top:28px">{e(label)}</h3>')
        for s in group:
            mark = IMPORTANCE_MARK.get(s["importance"], "")
            links = " · ".join(f'<a href="{e(u, quote=True)}">{e(n)}</a>' for n, u in _story_links(s, items))
            parts.append(
                f'<p style="margin:14px 0"><b>{mark} {e(s["title"])}</b> '
                f'<span style="color:#666;font-size:13px">{STATUS_LABEL[s["status"]]}</span><br>'
                f'{e(s["summary"])}{"<br>" if s["summary"] else ""}'
                f'<span style="font-size:13px">{links}</span></p>'
            )
    if brief["post_ideas"]:
        parts.append('<h3 style="margin-top:28px">Post ideas</h3><ul>')
        parts.extend(f"<li>{e(p)}</li>" for p in brief["post_ideas"])
        parts.append("</ul>")
    parts.append(f'<p style="color:#888;font-size:12px;margin-top:30px">{e(meta["footer"])}')
    if meta.get("warning"):
        parts.append(f'<br>⚠️ {e(meta["warning"])}')
    parts.append("</p></div>")
    return "".join(parts)


def render_markdown(brief, items, companies, meta):
    out = [f"# AI News Brief · {meta['when']}", "", f"**{brief['headline']}**", ""]
    for label, group in _sections(brief, companies):
        out += [f"## {label}", ""]
        for s in group:
            mark = IMPORTANCE_MARK.get(s["importance"], "-")
            links = " · ".join(f"[{n}]({u})" for n, u in _story_links(s, items))
            out.append(f"{mark} **{s['title']}** ({STATUS_LABEL[s['status']]})  ")
            if s["summary"]:
                out.append(f"  {s['summary']}  ")
            if links:
                out.append(f"  {links}")
            out.append("")
    if brief["post_ideas"]:
        out += ["## Post ideas", ""] + [f"- {p}" for p in brief["post_ideas"]] + [""]
    out.append(f"_{meta['footer']}_")
    if meta.get("warning"):
        out.append(f"\n_⚠️ {meta['warning']}_")
    return "\n".join(out) + "\n"
