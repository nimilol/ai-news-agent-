"""The Telegram message system: one consistent structure for everything the agent sends.

Every discovery becomes a *card* with a section, a priority and the same fields in the same order:

    headline · priority, verification and date · company / speaker · what happened · why it matters ·
    technical details · (optional) explained in plain language · sources

Cards are grouped into the ten sections below (only sections with something in them are shown), sorted by
priority, and packed into Telegram messages under the length limit without ever cutting a card or an HTML tag
in half. Long explanations and content ideas sit in collapsed ("expandable") quotes so messages stay short on a
phone but nothing is lost.

    Priorities   P0 Critical   confirmed flagship model releases, major developments (sent at once, with sound)
                 P1 High       significant upgrades, important announcements, big research (at once, with sound)
                 P2 Normal     industry news, benchmarks, discussions (at once in alerts, or in the digest; silent)
                 P3 Discovery  free models, repositories, experiments, tools (digest / Buzz / Builder picks; silent)
"""

import html
import re

SECTIONS = {
    "new_models": ("🚨", "NEW MODEL RELEASES"),
    "model_updates": ("🔄", "MODEL UPDATES AND IMPROVEMENTS"),
    "research": ("🧠", "AI RESEARCH AND BENCHMARKS"),
    "industry": ("📰", "AI INDUSTRY NEWS"),
    "discussions": ("🔥", "TRENDING AI DISCUSSIONS"),
    "free": ("🆓", "FREE AI MODELS AND TOOLS"),
    "tools": ("🛠", "NEW AI TOOLS AND PROJECTS"),
    "podcasts": ("🎙", "AI PODCASTS AND INTERVIEWS"),
    "content": ("💡", "CONTENT OPPORTUNITIES"),
    "health": ("⚙️", "AGENT HEALTH AND SYSTEM NOTIFICATIONS"),
}
STORY_SECTIONS = [k for k in SECTIONS if k not in ("content", "health")]
PRIORITIES = {"P0": ("🔴", "Critical"), "P1": ("🟠", "High"), "P2": ("🔵", "Normal"), "P3": ("⚪", "Discovery")}
PRIORITY_RANK = {p: n for n, p in enumerate(PRIORITIES)}
EXPLAIN_QUESTIONS = [
    ("introduced", "What was introduced"),
    ("does", "What it actually does"),
    ("problem", "What problem it solves"),
    ("different", "How it differs from before"),
    ("who", "Who benefits"),
    ("care", "Why creators and developers should care"),
]
CONTENT_KINDS = [("news", "A. NEWS", "a timely video on what happened"),
                 ("education", "B. EDUCATION", "a video explaining the technology"),
                 ("analysis", "C. ANALYSIS", "a comparison, practical test or opinion")]
LIMIT = 3900          # Telegram allows 4096 characters; leave room for the "(1/3)" page marker
ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre", "blockquote",
                "tg-spoiler", "span"}

e = html.escape


# ----------------------------------------------------------------------------------------------- small helpers

def tg_len(text):
    """Length as Telegram counts it (UTF-16 code units; emoji count twice). Tags are included, which is safe."""
    return len(text.encode("utf-16-le")) // 2


def clip(text, limit):
    """Shorten plain text (before escaping) at a word boundary, so an entity or tag is never cut."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def link(name, url):
    return f'<a href="{e(url, quote=True)}">{e(name)}</a>' if url else e(name)


def section_heading(key, continued=False):
    emoji, title = SECTIONS[key]
    return f"<b>{emoji} {title}</b>" + (" <i>(continued)</i>" if continued else "")


def priority_label(p):
    emoji, name = PRIORITIES.get(p, PRIORITIES["P2"])
    return f"{emoji} {p} {name}"


def best_priority(priorities):
    ranked = [p for p in priorities if p in PRIORITY_RANK]
    return min(ranked, key=PRIORITY_RANK.get) if ranked else "P3"


def is_silent(priority, config=None):
    """P2 and P3 arrive without a sound (configurable: telegram.silent_from in config.yaml)."""
    start = str(((config or {}).get("telegram") or {}).get("silent_from", "P2")).upper()
    start_rank = PRIORITY_RANK.get(start, 99 if re.fullmatch(r"P\d+", start) else 2)  # P4 = never silent
    return PRIORITY_RANK.get(priority, 3) >= start_rank


def human_date(value):
    """'2026-11-12' → '12 Nov 2026'; '2026-11' → 'Nov 2026'; anything else is shown as given."""
    from datetime import date

    if not value:
        return ""
    v = str(value)
    try:
        if len(v) == 7:
            return date.fromisoformat(v + "-01").strftime("%b %Y")
        return date.fromisoformat(v[:10]).strftime("%-d %b %Y")
    except ValueError:
        return v


# ----------------------------------------------------------------------------------------------- cards

def card(section, priority, headline, *, company=None, speaker=None, what=None, why=None, details=(),
         sources=(), date=None, verification=None, explain=None, update=None):
    """A story card (a plain dict, easy to build from any producer and to test)."""
    return {"section": section if section in SECTIONS else "industry",
            "priority": priority if priority in PRIORITIES else "P2", "headline": headline, "company": company,
            "speaker": speaker, "what": what, "why": why, "details": [d for d in details if d],
            "sources": [(n, u) for n, u in sources if u or n], "date": date, "verification": verification,
            "explain": explain or {}, "update": update}


def render_card(c, number=None, compact=False):
    """One card as Telegram HTML. compact=True keeps it to a few lines (instant alerts)."""
    title = f"{number}. " if number else ""
    lines = [f"<b>{title}{e(clip(c['headline'], 200))}</b>"]
    meta = [priority_label(c["priority"])]
    if c.get("verification"):
        meta.append(e(clip(c["verification"], 80)))
    if c.get("date"):
        meta.append(f"📅 {e(human_date(c['date']))}")
    lines.append("<i>" + " · ".join(meta) + "</i>")
    who = []
    if c.get("company"):
        who.append(f"🏢 {e(clip(c['company'], 60))}")
    if c.get("speaker"):
        who.append(f"🗣 {e(clip(c['speaker'], 80))}")
    if who:
        lines.append(" · ".join(who))
    if c.get("update"):
        lines.append(f"🔄 <b>Update:</b> {e(clip(c['update'], 300))}")
    if c.get("what"):
        lines.append(f"<b>What happened:</b> {e(clip(c['what'], 320 if compact else 700))}")
    if c.get("why"):
        lines.append(f"<b>Why it matters:</b> {e(clip(c['why'], 240 if compact else 500))}")
    details = c.get("details") or []
    if details:
        shown = details[:2] if compact else details[:5]
        lines.append("<b>Details:</b> " + " · ".join(e(clip(d, 160)) for d in shown))
    explained = explain_block(c.get("explain"))
    if explained and not compact:
        lines.append(explained)
    if c.get("sources"):
        lines.append("🔗 " + " · ".join(link(clip(n, 50), u) for n, u in c["sources"][:4]))
    return "\n".join(lines)


def explain_block(explain):
    """'Explained' in plain language: the six questions, in a collapsed quote. Empty answers are left out."""
    rows = [(label, (explain or {}).get(key)) for key, label in EXPLAIN_QUESTIONS]
    rows = [(label, v) for label, v in rows if v]
    if len(rows) < 2:  # a single line explains nothing: leave the block out
        return ""
    body = "\n".join(f"<b>{e(label)}:</b> {e(clip(v, 400))}" for label, v in rows)
    return f"<blockquote expandable><b>📘 Explained</b>\n{body}</blockquote>"


def content_block(opportunities, topic=None):
    """💡 CONTENT OPPORTUNITIES: A. NEWS, B. EDUCATION, C. ANALYSIS, each with title, angle, why it is interesting
    and what still needs research or testing. Suggestions only: nothing is created or published."""
    by_kind = {o.get("kind"): o for o in opportunities or [] if isinstance(o, dict)}
    parts = []
    for key, label, _ in CONTENT_KINDS:
        o = by_kind.get(key)
        if not o or not o.get("title"):
            continue
        rows = [f"<b>{label}: {e(clip(o['title'], 140))}</b>"]
        for field, name in (("angle", "Angle"), ("why", "Why it's interesting"), ("research", "Research or testing needed")):
            if o.get(field):
                rows.append(f"<i>{name}:</i> {e(clip(o[field], 300))}")
        parts.append("\n".join(rows))
    if not parts:
        return ""
    about = f"\n<i>Based on: {e(clip(topic, 120))}</i>" if topic else ""
    return (section_heading("content") + about + "\n<blockquote expandable>" + "\n\n".join(parts)
            + "\n\n<i>Ideas only. You decide what becomes a video or post.</i></blockquote>")


def clean_opportunities(raw, check=None):
    """Normalise AI-written content ideas ([{kind, title, angle, why, research}]); `check(text)` can veto any
    field (e.g. numbers that appear in no source)."""
    out = []
    for o in raw or []:
        if not isinstance(o, dict):
            continue
        kind = str(o.get("kind") or o.get("type") or "").strip().lower()
        kind = {"a": "news", "b": "education", "c": "analysis"}.get(kind, kind)
        if kind not in ("news", "education", "analysis") or any(x["kind"] == kind for x in out):
            continue
        item = {"kind": kind}
        for field in ("title", "angle", "why", "research"):
            value = clip(o.get(field), 300)
            if value and (check is None or check(value)):
                item[field] = value
        if item.get("title"):
            out.append(item)
    return out


def clean_explain(raw, check=None):
    out = {}
    for key, _ in EXPLAIN_QUESTIONS:
        value = clip((raw or {}).get(key) if isinstance(raw, dict) else "", 400)
        if value and (check is None or check(value)):
            out[key] = value
    return out


# ----------------------------------------------------------------------------------------------- reports

def priority_summary(cards):
    counts = {}
    for c in cards:
        counts[c["priority"]] = counts.get(c["priority"], 0) + 1
    return " · ".join(f"{PRIORITIES[p][0]} {counts[p]} {p}" for p in PRIORITIES if counts.get(p))


def report(title, cards, *, subtitle=None, when=None, content=None, content_topic=None, health=(), footer=None,
           compact=False, numbered=True, limit=LIMIT):
    """A full report (digest, alerts, Buzz, picks, recap) as a list of Telegram messages.

    Sections appear in the standard order and only when they have cards; cards are sorted by priority inside a
    section. `health` is a list of plain-text lines for the ⚙️ section."""
    head = f"<b>{e(title)}</b>" + (f" · {e(when)}" if when else "")
    if subtitle:
        head += f"\n<i>{e(clip(subtitle, 300))}</i>"
    summary = priority_summary(cards)
    if summary:
        head += f"\n{summary}"
    blocks = [(head, None)]
    n = 0
    for key in STORY_SECTIONS:
        group = sorted((c for c in cards if c["section"] == key), key=lambda c: PRIORITY_RANK[c["priority"]])
        for i, c in enumerate(group):
            n += 1
            text = render_card(c, n if numbered else None, compact)
            if i == 0:
                text = section_heading(key) + "\n" + text  # a heading never ends a message on its own
            blocks.append((text, key))
    extra = content_block(content, content_topic) if content else ""
    if extra:
        blocks.append((extra, "content"))
    if health:
        lines = "\n".join(f"• {e(clip(h, 300))}" for h in health)
        blocks.append((section_heading("health") + "\n" + lines, "health"))
    if footer:
        blocks.append((f"<i>{e(clip(footer, 600))}</i>", None))
    return paginate(blocks, limit)


def paginate(blocks, limit=LIMIT):
    """Pack (html, section) blocks into messages in order. A block is never split unless it alone is too long
    (then it is cut between lines with its open tags closed and reopened). A section that carries on into the
    next message gets its heading again, marked "(continued)". Several messages get a (1/3) marker."""
    messages, current, current_section = [], "", None
    for text, section in blocks:
        pieces = split_html(text, limit) if tg_len(text) > limit else [text]
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if current and tg_len(candidate) > limit:
                messages.append(current)
                if section and section == current_section and not piece.startswith("<b>" + SECTIONS[section][0]):
                    piece = section_heading(section, continued=True) + "\n" + piece
                candidate = piece
            current = candidate
            current_section = section or current_section
    if current:
        messages.append(current)
    if len(messages) > 1:
        messages = [f"{m}\n<i>({i}/{len(messages)})</i>" for i, m in enumerate(messages, 1)]
    return messages


_TAG = re.compile(r"<(/?)([a-zA-Z][\w-]*)([^>]*)>")


def split_html(text, limit=LIMIT):
    """Split long HTML between lines (or words) so every piece is valid on its own: tags still open at a cut are
    closed at the end of the piece and reopened at the start of the next."""
    pieces, stack, current = [], [], ""
    tokens = re.split(r"(\n)", text)
    words = []
    for t in tokens:
        if t == "\n" or tg_len(t) <= limit // 2:
            words.append(t)
        else:
            words += re.split(r"( )", t)  # a very long line: cut between words instead
    for w in words:
        reopen = "".join(open_tag for _, open_tag in stack)
        closing = "".join(f"</{name}>" for name, _ in reversed(stack))
        if current and tg_len(current + w + closing) > limit:
            pieces.append(current + closing)
            current = reopen
        current += w
        for m in _TAG.finditer(w):
            closing_tag, name = m.group(1) == "/", m.group(2).lower()
            if closing_tag:
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == name:
                        del stack[i]
                        break
            else:
                stack.append((name, m.group(0)))
    if current.strip():
        pieces.append(current + "".join(f"</{name}>" for name, _ in reversed(stack)))
    return [p.strip("\n") for p in pieces if p.strip()]


def check_html(text):
    """Problems Telegram would reject: unknown tags, unbalanced or crossed tags, raw '<' or '&'. [] = valid."""
    problems, stack = [], []
    for m in _TAG.finditer(text):
        closing, name = m.group(1) == "/", m.group(2).lower()
        if name not in ALLOWED_TAGS:
            problems.append(f"unsupported tag <{name}>")
            continue
        if closing:
            if not stack or stack[-1] != name:
                problems.append(f"unbalanced </{name}>")
            else:
                stack.pop()
        else:
            stack.append(name)
    if stack:
        problems.append(f"unclosed {stack}")
    plain = _TAG.sub("", text)
    if "<" in plain or ">" in plain:
        problems.append("raw < or > outside a tag")
    if re.search(r"&(?!(?:lt|gt|amp|quot|#\d+|#x[0-9a-fA-F]+);)", plain):
        problems.append("raw & that is not an entity")
    return problems


def strip_html(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text))
