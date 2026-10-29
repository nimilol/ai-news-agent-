"""Weekly recap: the biggest stories of the last 7 days, from the saved briefs in digests/."""

import html
import json
import re
from datetime import timedelta

from . import llm
from .common import ROOT, clean_text, now_utc
from .digest import split_messages

WEEKLY_PROMPT = """You write the weekly recap for an AI news creator who covers Anthropic (Claude),
OpenAI (GPT), xAI (Grok) and Zhipu / Z.ai (GLM). You get the numbered stories from this week's briefs.

Answer with JSON only:
{"headline": "the week in one sentence",
 "stories": [{"item": 1, "title": "short headline", "summary": "1-2 sentences on what happened and why it matters"}],
 "post_ideas": ["up to 5 post ideas for a weekly recap video or thread"]}

Rules: pick the 10 most important stories of the week, most important first. Merge duplicates (the same
story often appears in several briefs; use the first item number). Use only facts in the stories."""

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
            if m and section and section != "Post ideas":
                current = {"title": m.group(3), "label": m.group(4), "summary": "", "url": "", "section": section,
                           "hot": m.group(1) == "🔥", "date": path.stem[:10], "interview": bool(m.group(2))}
                stories.append(current)
            elif current and line.startswith("  ") and line.strip():
                link = LINK.search(line)
                if link and not current["url"]:
                    current["url"] = link.group(2)
                elif not link and not current["summary"]:
                    current["summary"] = clean_text(line, 400)
            elif not line.strip():
                current = None
    return stories


def recap(config, stories):
    """Returns (headline, [(story, title, summary)], post_ideas, model)."""
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
                               clean_text(str(p.get("summary", "")), 500)))
        if picked:
            ideas = [clean_text(str(i), 250) for i in (data.get("post_ideas") or [])[:5]]
            return clean_text(str(data.get("headline", "")), 300), picked[:10], ideas, model
    except Exception as e:  # noqa: BLE001
        print(f"AI recap failed, listing the top stories instead: {str(e)[:200]}")
    seen, picked = set(), []
    for s in important:
        key = s["title"].lower()
        if key not in seen:
            seen.add(key)
            picked.append((s, s["title"], s["summary"]))
    return "This week's top stories", picked[:12], [], "none"


def render(headline, picked, ideas, model, count):
    e = html.escape
    blocks = [f"<b>🗓 Weekly recap</b> · week ending {now_utc():%d %b}\n<i>{e(headline)}</i>"]
    for i, (s, title, summary) in enumerate(picked, 1):
        text = f"\n{i}. <b>{e(title)}</b> <i>({e(s['section'])})</i>"
        if summary:
            text += f"\n{e(summary)}"
        if s["url"]:
            text += f"\n↳ <a href=\"{e(s['url'], quote=True)}\">source</a>"
        blocks.append(text)
    if ideas:
        blocks.append("\n<b>💡 Recap post ideas</b>\n" + "\n".join(f"• {e(i)}" for i in ideas))
    blocks.append(f"\n<i>From {count} stories in this week's briefs · written by {e(model)}</i>")
    return split_messages(blocks)
