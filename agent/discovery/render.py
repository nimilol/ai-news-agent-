"""The 🚀 AI SKILLS & OPEN-SOURCE DISCOVERIES Telegram batch (HTML, built on agent/messages.py)."""

from .. import messages as msg
from ..common import now_utc

e = msg.e
GROUP_OF = {"agent_building": "agent", "skills": "skills", "prompting": "skills", "automation": "automation",
            "hackathon": "projects", "app_development": "projects", "creator_tools": "creator",
            "productivity": "productivity"}
GROUPS = [("agent", "🧠", "AI AGENT BUILDING"), ("skills", "🛠", "AI SKILLS AND PROMPTS"),
          ("automation", "⚡", "AI AUTOMATION AND WORKFLOWS"), ("projects", "🏗", "HACKATHON AND PROJECT RESOURCES"),
          ("creator", "🎬", "AI CREATOR RESOURCES"), ("productivity", "💻", "DEVELOPER PRODUCTIVITY")]
PRIORITY_NAMES = {"P0": "Exceptional discovery", "P1": "Highly useful", "P2": "Worth exploring", "P3": "Interesting discovery"}
CATEGORY_LABEL = {"agent_building": "agent building", "skills": "skills and instructions", "prompting": "prompt engineering",
                  "app_development": "app development", "automation": "automation", "hackathon": "hackathon projects",
                  "creator_tools": "creator tools", "productivity": "developer productivity"}
LICENCE_TEXT = {"FREE AND OPEN SOURCE": "Free and open source", "FREE TO USE, CLOSED SOURCE": "Free to use, closed source",
                "FREE TIER AVAILABLE": "Free tier available", "OPEN SOURCE WITH PAID OPTIONAL SERVICES":
                "Open source, with paid services it can use", "PAID OR RESTRICTED": "Paid or restricted",
                "LICENCE UNCLEAR": "Licence unclear"}


def _ago(iso):
    from ..common import parse_date

    d = parse_date(iso)
    if not d:
        return None
    days = (now_utc() - d).days
    return "updated today" if days <= 0 else f"updated {days} day{'s' if days != 1 else ''} ago" if days < 60 \
        else f"updated {days // 30} months ago"


def signals(c, ev):
    bits = []
    if c.get("stars"):
        bits.append(f"★ {c['stars']:,}")
    if c.get("forks"):
        bits.append(f"{c['forks']:,} forks")
    if c.get("likes"):
        bits.append(f"♥ {c['likes']:,}")
    if c.get("downloads"):
        bits.append(f"{c['downloads']:,} downloads")
    if c.get("open_issues") is not None and c["platform"] == "github":
        bits.append(f"{c['open_issues']:,} open issues")
    if ev.get("contributors"):
        bits.append(f"{ev['contributors']} contributors")
    ago = _ago(c.get("pushed_at"))
    if ago:
        bits.append(ago)
    return " · ".join(bits)


def cost(ev):
    text = LICENCE_TEXT[ev["licence_class"]]
    needs = ev.get("requirements") or []
    return text + (f" (needs: {', '.join(needs)})" if needs else "")


def requirements_text(ev):
    needs = ev.get("requirements") or []
    return ", ".join(needs) if needs else "no paid API, subscription or GPU mentioned in its README"


def _row(label, value, code=False):
    if not value:
        return None
    v = f"<code>{e(msg.clip(value, 160))}</code>" if code else e(msg.clip(value, 320))
    return f"<b>{label}:</b> {v}"


def card(item, number):
    c, ev, ex = item["candidate"], item["evaluation"], item["explanation"]
    group = GROUP_OF[ev["primary"]]
    platform = {"github": "Repository", "huggingface": "Hugging Face", "gitlab": "Repository",
                "codeberg": "Repository"}.get(c["platform"], "Original link")
    link = msg.link(c["url"].replace("https://", ""), c["url"])
    name = c.get("name") or c.get("display")
    head = [f"<b>{number}. {e(name)}</b>",
            f"<i>{msg.PRIORITIES[ev['priority']][0]} {ev['priority']} {PRIORITY_NAMES[ev['priority']]} · score "
            f"{ev['score']}/100</i>"]
    sig = signals(c, ev)
    if sig:
        head.append(f"<i>{e(sig)}</i>")
    rows = {
        "agent": [("Resource", name), ("Developer", c.get("owner")), ("What it does", ex["what"]),
                  ("Why it matters", ex["why"]), ("Practical application", ex["practical"]),
                  ("Compatibility", ", ".join(ev.get("compatibility") or []) or ex.get("integrate")),
                  ("Cost", cost(ev)), ("Licence", ev["licence_note"])],
        "skills": [("Resource", name), ("What it does", ex["what"]), ("Why it is useful", ex["why"]),
                   ("Where it can be integrated", ex.get("integrate") or ", ".join(ev.get("compatibility") or [])),
                   ("Installation", ev.get("install") or "see the README's setup section"), ("Cost", cost(ev))],
        "automation": [("Resource", name), ("What it does", ex["what"]),
                       ("Workflow it improves", ex.get("workflow") or ex["problem"]), ("Practical use", ex["practical"]),
                       ("Requirements", requirements_text(ev))],
        "projects": [("Resource", name), ("What you can build", ex.get("build") or ex["practical"]),
                     ("Why it is worth exploring", ex["why"]),
                     ("Technologies", ", ".join(x for x in [c.get("language")] + (ev.get("compatibility") or []) if x)),
                     ("Getting started", ev.get("install") or "see the README's setup section")],
        "creator": [("Resource", name), ("What it does", ex["what"]),
                    ("How creators can use it", ex.get("creators") or ex["practical"]),
                    ("Requirements", requirements_text(ev))],
        "productivity": [("Resource", name), ("What it does", ex["what"]),
                         ("Workflow it improves", ex.get("workflow") or ex["problem"]),
                         ("Compatibility", ", ".join(ev.get("compatibility") or [])), ("Cost", cost(ev))],
    }[group]
    lines = head + [r for r in (_row(lbl, v, code=lbl in ("Installation", "Getting started") and v == ev.get("install"))
                                for lbl, v in rows[1:]) if r]
    if ex.get("relevance"):
        lines.append(f"💼 <i>{e(msg.clip(ex['relevance'], 260))}</i>")
    others = [CATEGORY_LABEL[k] for k in ev["categories"][1:3] if GROUP_OF[k] != group]
    if others:
        lines.append(f"<i>Also useful for: {e(', '.join(others))}</i>")
    for w in ev.get("warnings") or []:
        lines.append(f"⚠️ <i>{e(w)}</i>")
    lines.append(f"🔗 <b>{'Repository' if group == 'agent' else 'Original link'}:</b> {link}"
                 if platform != "Hugging Face" else f"🔗 <b>Hugging Face:</b> {link}")
    return "\n".join(lines)


def update_card(u):
    lines = [f"<b>{e(u['name'])}</b> · {e(u['version_from'] or 'first release')} → {e(u['version_to'])}",
             f"<b>What changed:</b> {e(msg.clip(u['summary'], 400))}"]
    if u.get("url"):
        lines.append("🔗 " + msg.link("release notes", u["url"]))
    return "\n".join(lines)


def best_block(item, best):
    c, ev, ex = item["candidate"], item["evaluation"], item["explanation"]
    why = best.get("why_first") or (
        f"Highest score this batch ({ev['score']}/100): {LICENCE_TEXT[ev['licence_class']].lower()}, "
        f"{ev['docs']} documentation, {ev['maintenance']}"
        + (f", and directly useful for {ev['relevance']['project']}" if ev.get("relevance") else "") + ".")
    problem = best.get("problem") or ex["problem"]
    build = best.get("build") or ex.get("build") or ex["practical"]
    return ("<b>🏆 BEST DISCOVERY</b>\n"
            f"<b>{e(c.get('name'))}</b> · {msg.link('open', c['url'])}\n"
            f"<b>Why it ranked first:</b> {e(msg.clip(why, 400))}\n"
            f"<b>What problem it solves:</b> {e(msg.clip(problem, 300))}\n"
            f"<b>What you could build or improve:</b> {e(msg.clip(build, 300))}")


def render(items, updates, best_i, best, period_start, period_end, model):
    """items: [{"candidate", "evaluation", "explanation"}] ranked best first (one entry per resource)."""
    head = ("<b>🚀 AI SKILLS &amp; OPEN-SOURCE DISCOVERIES</b>\n"
            f"<b>Date:</b> {now_utc():%-d %b %Y, %H:%M} UTC\n"
            f"<b>Discovery period:</b> {period_start:%-d %b %H:%M} – {period_end:%-d %b %H:%M} UTC\n"
            f"<b>New discoveries:</b> {len(items)}" + (f" · <b>updates:</b> {len(updates)}" if updates else ""))
    blocks = [(head, None)]
    n = 0
    for key, emoji, title in GROUPS:
        group = [it for it in items if GROUP_OF[it["evaluation"]["primary"]] == key]
        for i, it in enumerate(group):
            n += 1
            text = card(it, n)
            blocks.append(((f"<b>{emoji} {title}</b>\n" if i == 0 else "") + text, None))
    if updates:
        blocks.append(("<b>🔄 UPDATES TO EARLIER DISCOVERIES</b>\n" + "\n\n".join(update_card(u) for u in updates), None))
    if items and best_i is not None:
        blocks.append((best_block(items[best_i], best), None))
    footer = ("Discovered, checked and ranked automatically; nothing was installed or run. Read a repository's code "
              "and licence before using it." + (f" Explanations by {model}." if model else ""))
    blocks.append((f"<i>{e(footer)}</i>", None))
    return msg.paginate(blocks)
