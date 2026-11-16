"""Turns collected items into a briefing: the AI prompt, reading the AI's answer, and rendering the result for
Telegram (HTML, in the standard sections of agent/messages.py), email (HTML + text) and the Markdown archive."""

import html
import json
import re

from . import messages as msg
from .accuracy import badges, first_seen, publisher
from .common import TIER_RANK, clean_text, parse_date

SECTION_KEYS = ["new_models", "model_updates", "research", "industry", "discussions", "free", "tools", "podcasts"]

SYSTEM_PROMPT = """You are the research assistant of an AI news creator and educator who covers four AI companies:
Anthropic (Claude), OpenAI (GPT, ChatGPT), xAI (Grok) and Zhipu / Z.ai (GLM).
You receive a numbered list of news items collected over the last hours from official company sites,
leak trackers, communities, newsletters and the press. The creator wants to understand each development well
enough to teach it, so explain, don't just repeat the announcement.

Answer with JSON only, in exactly this shape:
{
  "headline": "the single most important development, one sentence ('Quiet period' if nothing notable)",
  "stories": [
    {
      "company": "anthropic | openai | xai | zhipu | free_models | industry",
      "section": "new_models | model_updates | research | industry | discussions | free | tools | podcasts",
      "title": "short factual headline, at most 12 words",
      "summary": "2-3 sentences: what happened. Keep concrete names, numbers, dates.",
      "why": "one sentence: why it matters to people who build with or teach AI",
      "details": ["up to 3 short technical facts from the items (model ids, limits, prices, dates)"],
      "speaker": "for interviews, research and discussions: who said it or where it was published, else empty",
      "status": "official | report | rumor",
      "importance": 1,
      "items": [1, 2],
      "update": false,
      "explain": {"introduced": "", "does": "", "problem": "", "different": "", "who": "", "care": ""}
    }
  ],
  "content": [
    {"kind": "news", "title": "", "angle": "", "why": "", "research": ""},
    {"kind": "education", "title": "", "angle": "", "why": "", "research": ""},
    {"kind": "analysis", "title": "", "angle": "", "why": "", "research": ""}
  ]
}

Rules:
- Merge items about the same news into one story and list all of their numbers in "items". Only merge items
  that are clearly about the same event; when unsure, keep them as separate stories.
- Use only facts found in the items. Never invent details, numbers, quotes or links. When an item has an
  "Article:" text, base the summary on the article, not just the headline.
- A list of headlines already sent recently may follow the items. Do not repeat those stories. Include one again
  only if the new items add a real development, and then set "update": true and say in the summary what is new.
- section: "new_models" only for a new model the company itself released; "model_updates" for upgrades, new
  versions, pricing, limits and deprecations of existing models; "research" for papers, benchmarks and evaluations;
  "industry" for business, policy, people, funding, leaks and rumours about upcoming models; "discussions" for
  notable community debates (Reddit, Hacker News); "free" for models or tools that became free; "tools" for new
  developer tools and open-source projects; "podcasts" for interviews, podcasts, AMAs and talks.
- status: "official" when an item comes from the company's own official source (tier official);
  "report" when it comes from the press or newsletters; "rumor" for leaks, stealth models,
  unconfirmed claims and community speculation.
- importance: 5 = new flagship model, major launch or shutdown, pricing/policy change affecting many users;
  4 = notable feature or model update, big deal, funding, outage, major research result; 3 = smaller update or
  interesting report; 2 = minor; 1 = noise.
- explain: fill it ONLY for stories with importance 4 or 5, in plain language with correct technical terms:
  what was introduced, what it actually does, what problem it solves, how it differs from what existed before,
  who benefits, and why a creator or developer should care. Leave fields empty when the items don't say.
  For other stories leave "explain" empty.
- content: three video ideas about the single most important story: "news" (a timely video on what happened),
  "education" (explaining the technology), "analysis" (a comparison, practical test or opinion). Each has a title,
  the main angle, why it is interesting, and what still needs to be researched or tested before making it.
- Apart from free models, leave out items that are not really about these companies, and noise (memes, help requests,
  job posts, routine SDK patch releases). Returning few stories is fine.
- "industry" as a company is for stories involving several of the companies, or the AI field as a whole.
- Items from a "models page", "pricing", "deprecations" or "plans" source are changes the company made to
  that page. Say exactly what changed (a new model, a price, a retirement date, a limit); these are often
  unannounced and very newsworthy (importance 4-5 for prices, new models and retirements).
- Items marked "interview" are interviews, podcasts, AMAs or talks with people who work in AI (executives,
  researchers, engineers, investors), mostly from YouTube and Reddit. Keep them when the guest says something
  newsworthy about these companies: a plan, a date, a number, a strong opinion. Title them like
  "Sam Altman on ...: ..." and put the guest and where they spoke in "speaker"
  (section "podcasts", status "report"; importance 3, or 4 when a company leader reveals something new).
- Status-page items: include only real outages that affect many users (importance 4). Skip short or minor
  incidents.
- "free_models" is for new models the creator can use for free (items from "OpenRouter free models" or
  "OpenCode free models"), from any company. Keep these. Group several into one story when there are many, and
  name each model, who made it and where it is free. Use section "free", status "official" and importance 3
  (4 if it is a well-known model from a major lab).
- Sort stories by importance, highest first. At most 25 stories."""

STATUS_LABEL = {"official": "✅ Official", "report": "📰 Report", "rumor": "👀 Rumor"}
IMPORTANCE_MARK = {5: "🔥", 4: "⭐"}
RELEASE_WORDS = re.compile(r"\b(introduc\w*|launch\w*|releas\w*|unveil\w*|now available|generally available)\b", re.I)
DOCS_WORDS = re.compile(r"\b(pric\w*|deprecat\w*|retir\w*|limit\w*|models page|plans?)\b", re.I)


def rank_items(items):
    return sorted(items, key=lambda i: (TIER_RANK.get(i["tier"], 9), -(_ts(i["published"]))))


def _ts(iso):
    d = parse_date(iso)
    return d.timestamp() if d else 9e12  # undated items are brand-new by definition


def build_user_message(items, now, summary_chars=400, article_chars=1500, recent=None):
    lines = [f"Current time: {now:%Y-%m-%d %H:%M} UTC. {len(items)} items:\n"]
    for n, it in enumerate(items, 1):
        date = (it["published"] or "")[:10] or "new"
        companies = ",".join(it["companies"]) or "-"
        kind = " · interview" if it.get("interview") else ""
        lines.append(f"[{n}] ({it['tier']}{kind} · {it['source']} · {publisher(it)} · {companies} · {date}) {it['title']}")
        if it["summary"]:
            lines.append(f"    {clean_text(it['summary'], summary_chars)}")
        if it.get("article") and article_chars:
            lines.append(f"    Article: {clean_text(it['article'], article_chars)}")
    if recent:
        lines.append("\nAlready sent recently (do not repeat unless there is a new development):")
        lines.extend(f"- {t}" for t in recent[-60:])
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------- sections & priority

def story_section(story, items):
    """The section a story belongs in. The AI suggests one; these rules have the last word: interviews are
    podcasts, free models are free, and only an official release is a "new model release" (a leak or press
    report about an upcoming model is industry news)."""
    refs = [items[n - 1] for n in story["items"]]
    if story.get("interview"):
        return "podcasts"
    if story["company"] == "free_models":
        return "free"
    section = story.get("section") if story.get("section") in SECTION_KEYS else None
    if section is None:
        tiers = {it["tier"] for it in refs}
        text = " ".join(it["title"] for it in refs)
        if "official" in tiers:
            section = "new_models" if RELEASE_WORDS.search(text) else "model_updates" if DOCS_WORDS.search(text) \
                else "industry"
        elif tiers <= {"community"}:
            section = "discussions"
        else:
            section = "industry"
    if section == "new_models" and story["status"] != "official":
        section = "industry"
    return section


def story_priority(story):
    """P0 only for an official new model of importance 5; free models and tools are discoveries (P3)."""
    imp, status, section = story["importance"], story["status"], story["section"]
    if section in ("free", "tools"):
        return "P2" if imp >= 5 else "P3"
    if imp >= 5 and status == "official" and section == "new_models":
        return "P0"
    if imp >= 5:
        return "P1"
    if imp == 4:
        return "P2" if section in ("industry", "discussions", "podcasts") and status != "official" else "P1"
    return "P2" if imp == 3 else "P3"


def _corpus(story, items):
    return " ".join(f"{items[n - 1]['title']} {items[n - 1]['summary']} {items[n - 1].get('article', '')}"
                    for n in story["items"])


def parse_answer(text, items, companies):
    """Read the AI's JSON. Anything unusable raises ValueError so the caller can fall back."""
    from .releases.analyze import numbers_supported

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
        story = {
            "company": company if company in companies else "industry",
            "title": clean_text(str(s.get("title", "")), 200) or items[refs[0] - 1]["title"],
            "summary": clean_text(str(s.get("summary", "")), 900),
            "status": s.get("status") if s.get("status") in STATUS_LABEL else "report",
            "importance": importance,
            "items": refs,
            "update": s.get("update") is True,
            "interview": any(items[n - 1].get("interview") for n in refs),
            "section": str(s.get("section", "")).strip().lower(),
            "speaker": clean_text(str(s.get("speaker") or ""), 120),
        }
        corpus = _corpus(story, items)
        why = clean_text(str(s.get("why") or ""), 400)
        story["why"] = why if numbers_supported(why, corpus) else ""
        story["details"] = [clean_text(str(d), 160) for d in (s.get("details") or [])[:3]
                            if d and numbers_supported(str(d), corpus)]
        story["explain"] = msg.clean_explain(s.get("explain"), lambda v: numbers_supported(v, corpus)) \
            if importance >= 4 else {}
        story["section"] = story_section(story, items)
        story["priority"] = story_priority(story)
        stories.append(story)
    if not stories and items:
        raise ValueError("the answer had no usable stories")
    all_text = " ".join(f"{it['title']} {it['summary']} {it.get('article', '')}" for it in items)
    content = msg.clean_opportunities(data.get("content"), lambda v: numbers_supported(v, all_text))
    return {
        "headline": clean_text(str(data.get("headline", "")), 300),
        "stories": stories,
        "content": content,
        "post_ideas": [clean_text(str(p), 300) for p in (data.get("post_ideas") or [])[:3] if p],
    }


def fallback_brief(items, companies, per_company=10):
    """No AI available: list the headlines, grouped by section."""
    stories, counts = [], {}
    for n, it in enumerate(items, 1):
        company = it["companies"][0] if it["companies"] else "industry"
        if counts.get(company, 0) >= per_company:
            continue
        counts[company] = counts.get(company, 0) + 1
        tier = it["tier"]
        story = {
            "company": company if company in companies else "industry",
            "title": it["title"],
            "summary": clean_text(it["summary"], 220),
            "status": "official" if tier == "official" else "rumor" if tier in ("leak", "community") else "report",
            "importance": 4 if tier == "official" else 3 if tier == "leak" else 2,
            "items": [n],
            "interview": bool(it.get("interview")),
            "why": "", "details": [], "speaker": it["source"] if it.get("interview") else "", "explain": {},
        }
        story["section"] = story_section(story, items)
        story["priority"] = story_priority(story)
        stories.append(story)
    return {"headline": "AI summary unavailable this run, so here are the raw headlines.", "stories": stories,
            "content": [], "post_ideas": []}


def _story_links(story, items, max_links=3):
    links, seen = [], set()
    for n in story["items"]:
        it = items[n - 1]
        if it["url"] and it["url"] not in seen:
            seen.add(it["url"])
            links.append((publisher(it), it["url"]))
    return links[:max_links]


def _ensure_sections(brief, items):
    """Briefs built elsewhere (tests, older code) may lack section/priority: fill them in."""
    for s in brief["stories"]:
        s.setdefault("why", "")
        s.setdefault("details", [])
        s.setdefault("speaker", "")
        s.setdefault("explain", {})
        if s.get("section") not in SECTION_KEYS or "priority" not in s:
            s["section"] = story_section(s, items)
            s["priority"] = story_priority(s)


def story_card(s, items, companies):
    seen = first_seen(s, items)
    trust = badges(s, items)
    verification = STATUS_LABEL[s["status"]] + (f" · {trust}" if trust else "")
    label = companies[s["company"]]["label"] if s["company"] in companies and s["company"] != "free_models" else None
    date = None if "🕐" in trust else (seen.date().isoformat() if seen else None)  # the trust line has the time
    return msg.card(
        s["section"], s["priority"], s["title"], company=label, speaker=s.get("speaker"), what=s["summary"],
        why=s.get("why"), details=s.get("details", []), sources=_story_links(s, items),
        date=date, verification=verification, explain=s.get("explain"),
        update="new development on a story sent earlier" if s.get("update") else None)


def brief_priority(brief):
    return msg.best_priority(s["priority"] for s in brief["stories"])


def render_telegram(brief, items, companies, meta, limit=msg.LIMIT):
    """Telegram HTML in the standard sections, split under Telegram's 4096-character limit."""
    _ensure_sections(brief, items)
    cards = [story_card(s, items, companies) for s in brief["stories"]]
    content = brief.get("content") or []
    top = max(brief["stories"], key=lambda s: s["importance"])["title"] if brief["stories"] and content else None
    if not content and brief.get("post_ideas"):  # older answers: keep the post ideas as news ideas
        content = [{"kind": k, "title": p} for k, p in zip(("news", "education", "analysis"), brief["post_ideas"])]
    health = [meta["warning"]] if meta.get("warning") else []
    footer = meta["footer"] + (f" · already sent as alerts: {meta['covered']}" if meta.get("covered") else "")
    return msg.report("🛰 AI News Brief", cards, when=meta["when"], subtitle=brief["headline"], content=content,
                      content_topic=top, health=health, footer=footer, limit=limit)


def split_messages(blocks, limit=msg.LIMIT):
    """Pack text blocks into as few messages as fit under Telegram's 4096-character limit. A block that is too long
    on its own is split between lines with its HTML kept valid (never cut in the middle of a tag)."""
    messages, current = [], ""
    for b in blocks:
        for piece in (msg.split_html(b, limit) if msg.tg_len(b) > limit else [b]):
            if current and msg.tg_len(current) + msg.tg_len(piece) + 1 > limit:
                messages.append(current)
                current = ""
            current = f"{current}\n{piece}" if current else piece
    if current:
        messages.append(current)
    return messages


def _by_section(brief):
    for key in SECTION_KEYS:
        group = [s for s in brief["stories"] if s["section"] == key]
        if group:
            group.sort(key=lambda s: msg.PRIORITY_RANK[s["priority"]])
            emoji, title = msg.SECTIONS[key]
            yield f"{emoji} {title}", group


def render_email_html(brief, items, companies, meta):
    _ensure_sections(brief, items)
    e = html.escape
    parts = [
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:680px;margin:auto;color:#1a1a1a;line-height:1.5">',
        f'<h2 style="margin-bottom:4px">AI News Brief</h2><div style="color:#666">{e(meta["when"])}</div>',
        f'<p style="font-size:17px"><b>{e(brief["headline"])}</b></p>',
    ]
    for label, group in _by_section(brief):
        parts.append(f'<h3 style="border-bottom:2px solid #ddd;padding-bottom:4px;margin-top:28px">{e(label)}</h3>')
        for s in group:
            company = companies[s["company"]]["label"] if s["company"] in companies else "Across the industry"
            links = " · ".join(f'<a href="{e(u, quote=True)}">{e(n)}</a>' for n, u in _story_links(s, items))
            why = f'<br><i>Why it matters: {e(s["why"])}</i>' if s.get("why") else ""
            parts.append(
                f'<p style="margin:14px 0"><b>{e(s["title"])}</b> '
                f'<span style="color:#666;font-size:13px">{msg.priority_label(s["priority"])} · {e(company)} · '
                f'{STATUS_LABEL[s["status"]]} · {e(badges(s, items))}</span><br>'
                f'{e(s["summary"])}{why}<br><span style="font-size:13px">{links}</span></p>')
    content = brief.get("content") or []
    if content:
        parts.append('<h3 style="margin-top:28px">💡 Content opportunities</h3><ul>')
        for o in content:
            parts.append(f"<li><b>{e(o['kind'].title())}: {e(o['title'])}</b> {e(o.get('angle', ''))}</li>")
        parts.append("</ul>")
    parts.append(f'<p style="color:#888;font-size:12px;margin-top:30px">{e(meta["footer"])}')
    if meta.get("warning"):
        parts.append(f'<br>⚠️ {e(meta["warning"])}')
    parts.append("</p></div>")
    return "".join(parts)


def render_markdown(brief, items, companies, meta):
    """The archive in digests/. The story lines keep their format ("mark **title** (status · ...)"): the weekly
    recap reads them back."""
    _ensure_sections(brief, items)
    out = [f"# AI News Brief · {meta['when']}", "", f"**{brief['headline']}**", ""]
    for label, group in _by_section(brief):
        out += [f"## {label}", ""]
        for s in group:
            mark = IMPORTANCE_MARK.get(s["importance"], "-") + (" 🎙" if s.get("interview") else "")
            links = " · ".join(f"[{n}]({u})" for n, u in _story_links(s, items))
            trust = badges(s, items)
            company = companies[s["company"]]["label"] if s["company"] in companies else "Industry"
            out.append(f"{mark} **{s['title']}** ({STATUS_LABEL[s['status']]} · {s['priority']} · {company}"
                       f"{' · ' + trust if trust else ''})  ")
            if s["summary"]:
                out.append(f"  {s['summary']}  ")
            if s.get("why"):
                out.append(f"  _Why it matters: {s['why']}_  ")
            if links:
                out.append(f"  {links}")
            out.append("")
    content = brief.get("content") or []
    if content:
        out += ["## Content opportunities", ""]
        for o in content:
            out.append(f"- **{o['kind'].title()}: {o['title']}** {o.get('angle', '')}".rstrip())
        out.append("")
    elif brief.get("post_ideas"):
        out += ["## Post ideas", ""] + [f"- {p}" for p in brief["post_ideas"]] + [""]
    out.append(f"_{meta['footer']}_")
    if meta.get("warning"):
        out.append(f"\n_⚠️ {meta['warning']}_")
    return "\n".join(out) + "\n"
