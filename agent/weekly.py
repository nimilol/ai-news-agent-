"""Weekly recap: the biggest stories of the last 7 days, from the saved briefs in digests/."""

import json
import re
from datetime import timedelta

from . import llm
from . import messages as msg
from .common import ROOT, clean_text, now_utc

LAST_WEEKLY = ROOT / "state" / "last_weekly.json"

WEEKLY_PROMPT = """You write the weekly recap for an AI news creator who covers Anthropic (Claude),
OpenAI (GPT), xAI (Grok) and Zhipu / Z.ai (GLM). You get the numbered stories from this week's briefs.

Answer with JSON only:
{"headline": "the week in one sentence",
 "stories": [{"item": 1, "title": "short headline", "summary": "1-2 sentences on what happened",
              "why": "one sentence: why it mattered this week"}],
 "content": [{"kind": "news | education | analysis", "title": "", "angle": "", "why": "", "research": ""}]}

Rules: pick the 10 most important stories of the week, most important first. Merge duplicates (the same
story often appears in several briefs; use the first item number). Use only facts in the stories.
"content": three ideas for a weekly video: "news" (the week's biggest story), "education" (explain the technology
behind it), "analysis" (a comparison or opinion worth investigating). Each with a title, the main angle, why it is
interesting, and what still needs research or testing."""

MARK = re.compile(r"^(🔥|⭐|-|•)( 🎙)? \*\*(.+?)\*\* \((.+?)\)\s*$")
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def read_week(days=7):
    """Stories from this week's briefs: [{title, label, summary, url, section, hot}]."""
    cutoff = (now_utc() - timedelta(days=days)).strftime("%Y-%m-%d_%H%M")
    stories = []
    for path in sorted((ROOT / "digests").glob("*.md")):
        if path.stem < cutoff:
            continue
        section, current = "", None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                section = line[3:].strip()
                continue
            m = MARK.match(line.strip())
            if m and section and section not in ("Post ideas", "Content opportunities"):
                prio = re.search(r"\bP[0-3]\b", m.group(4))
                current = {"title": m.group(3), "label": m.group(4), "summary": "", "url": "", "section": section,
                           "hot": m.group(1) == "🔥", "date": path.stem[:10], "interview": bool(m.group(2)),
                           "priority": prio.group(0) if prio else ("P1" if m.group(1) == "🔥" else "P2")}
                stories.append(current)
            elif current and line.startswith("  ") and line.strip():
                link = LINK.search(line)
                if link and not current["url"]:
                    current["url"] = link.group(2)
                elif line.strip().startswith("_Why it matters:"):
                    current["why"] = clean_text(line.strip().strip("_")[len("Why it matters:"):], 300)
                elif not link and not current["summary"]:
                    current["summary"] = clean_text(line, 400)
            elif not line.strip():
                current = None
    return stories


def recap(config, stories):
    """Returns (headline, [(story, title, summary, why)], content ideas, model)."""
    important = [s for s in stories if s["hot"] or s["label"].startswith("✅")] + \
                [s for s in stories if not (s["hot"] or s["label"].startswith("✅"))]

    def build(max_items=None, **_):
        return "\n".join(f"[{n}] ({s['date']} · {s['section']} · {s['label']}) {s['title']} :: {s['summary']}"
                         for n, s in enumerate(important[: max_items or 150], 1))
    try:
        answer, model = llm.complete(config, WEEKLY_PROMPT, build)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip())
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        picked = []
        for p in data.get("stories") or []:
            try:
                n = int(p.get("item"))
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(important):
                picked.append((important[n - 1], clean_text(str(p.get("title", "")), 200) or important[n - 1]["title"],
                               clean_text(str(p.get("summary", "")), 500), clean_text(str(p.get("why") or ""), 300)))
        if picked:
            ideas = msg.clean_opportunities(data.get("content"))
            return clean_text(str(data.get("headline", "")), 300), picked[:10], ideas, model
    except Exception as e:  # noqa: BLE001
        print(f"AI recap failed, listing the top stories instead: {str(e)[:200]}")
    seen, picked = set(), []
    for s in important:
        key = s["title"].lower()
        if key not in seen:
            seen.add(key)
            picked.append((s, s["title"], s["summary"], s.get("why", "")))
    return "This week's top stories", picked[:12], [], "none"


def _section_key(heading):
    """Archive heading → standard section ('🚨 NEW MODEL RELEASES' → new_models). Older archives used company
    headings; those stories go under industry news with the company kept."""
    for key, (emoji, title) in msg.SECTIONS.items():
        if title in heading.upper():
            return key, None
    return "industry", heading


def render(headline, picked, ideas, model, count):
    """The weekly intelligence report, in the standard sections (agent/messages.py)."""
    cards = []
    for s, title, summary, why in picked:
        section, company = _section_key(s["section"])
        if s.get("interview"):
            section = "podcasts"
        status = s["label"].split(" · ")[0]
        cards.append(msg.card(section, s.get("priority", "P2"), title, company=company, what=summary,
                              why=why or s.get("why"), sources=[("source", s["url"])] if s["url"] else [],
                              date=s["date"], verification=status))
    return msg.report("🗓 Weekly intelligence report", cards, when=f"week ending {now_utc():%d %b}", subtitle=headline,
                      content=ideas, content_topic=picked[0][1] if picked and ideas else None,
                      footer=f"From {count} stories in this week's briefs · written by {model}")


def save(messages):
    LAST_WEEKLY.parent.mkdir(exist_ok=True)
    LAST_WEEKLY.write_text(json.dumps(messages, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
