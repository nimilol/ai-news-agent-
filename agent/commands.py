"""Telegram commands you can send to your bot.

The agent isn't online all the time (it runs on GitHub's schedule), so it reads your messages on
each run: the hourly alert check and every brief. Replies arrive within about an hour.
Only messages from your own chat (TELEGRAM_CHAT_ID) are answered.
"""

import json

import requests

from . import freemodels
from .common import ROOT, env
from .deliver import send_telegram
from .state import State

HELP = (
    "<b>🛰 Your AI research assistant</b>\n"
    "I check your messages every hour, so replies can take up to an hour.\n\n"
    "<b>News</b>\n"
    "/latest · the most recent news brief\n"
    "/buzz · what's hot on GitHub, Hugging Face, Reddit and YouTube\n"
    "/repos · today's Builder picks\n"
    "/discoveries · the latest AI skills & open-source discoveries\n"
    "/weekly · the latest weekly news recap\n"
    "/ask your question · answered from this week's briefs\n\n"
    "<b>Models</b>\n"
    "/models · recent confirmed model releases\n"
    "/releases · the full release history (upgrades, availability, reviews)\n"
    "/weeklymodels · the latest Weekly Model Intelligence Report\n"
    "/compare model A vs model B · official specs side by side\n"
    "/free · models you can use for free right now\n\n"
    "<b>System</b>\n"
    "/sources · the official sources being monitored\n"
    "/health · runs, failing sources and alert delivery\n"
    "/help · this message\n\n"
    "Priorities: 🔴 P0 critical · 🟠 P1 high · 🔵 P2 normal · ⚪ P3 discovery (P2 and P3 arrive silently)."
)
COMMANDS = [
    {"command": "free", "description": "Free models you can use right now"},
    {"command": "latest", "description": "Resend the latest news brief"},
    {"command": "repos", "description": "Today's Builder picks"},
    {"command": "discoveries", "description": "Latest AI skills & open-source discoveries"},
    {"command": "buzz", "description": "What's hot: GitHub, Hugging Face, Reddit, YouTube"},
    {"command": "ask", "description": "Ask about this week's news"},
    {"command": "models", "description": "Recent confirmed model releases"},
    {"command": "releases", "description": "Model release history"},
    {"command": "compare", "description": "Compare two models: /compare A vs B"},
    {"command": "weekly", "description": "The latest weekly news recap"},
    {"command": "weeklymodels", "description": "The latest Weekly Model Intelligence Report"},
    {"command": "sources", "description": "Official sources being monitored"},
    {"command": "health", "description": "Agent health"},
    {"command": "news", "description": "Run a fresh news check now"},
    {"command": "help", "description": "What I can do"},
]
LAST_BRIEF = ROOT / "state" / "last_brief.json"
LAST_REPOS = ROOT / "state" / "last_repos.json"
LAST_BUZZ = ROOT / "state" / "last_buzz.json"
LAST_WEEKLY = ROOT / "state" / "last_weekly.json"
LAST_MODEL_WEEKLY = ROOT / "state" / "last_model_weekly.json"
LAST_DISCOVERY = ROOT / "state" / "last_discovery.json"
ASK_PROMPT = """You answer questions from an AI news creator using only the news briefs below (newest last).
Answer in at most 8 short lines of plain text. Name the date and source of each fact. If the briefs don't
cover the question, say so plainly instead of guessing."""


def save_json(path, messages):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(messages, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def save_last_brief(messages):
    save_json(LAST_BRIEF, messages)


def recent_briefs(days=7, max_chars=60000):
    """This week's saved briefs as one text, newest last, trimmed from the oldest end."""
    from datetime import timedelta

    from .common import now_utc

    cutoff = (now_utc() - timedelta(days=days)).strftime("%Y-%m-%d_%H%M")
    texts = [p.read_text(encoding="utf-8") for p in sorted((ROOT / "digests").glob("*.md")) if p.stem >= cutoff]
    return "\n\n".join(texts)[-max_chars:]


def answer_question(config, question):
    import html

    from . import llm

    briefs = recent_briefs()
    if not briefs:
        return "I don't have any saved briefs yet, so I can't answer from the news."
    text, _ = llm.complete(config, ASK_PROMPT,
                           lambda max_items=None, **_: f"Briefs:\n{briefs[-(12000 if max_items else 60000):]}\n\nQuestion: {question}")
    return f"<b>❓ {html.escape(question)}</b>\n{html.escape(text.strip())}"


def recent_releases():
    """The /releases reply: the latest model releases from the release monitor's history."""
    from .releases import config as rconfig
    from .releases.render import render_recent
    from .releases.store import Store

    return render_recent(Store().recent_releases(), rconfig.load()["companies"])


def recent_models():
    """The /models reply: confirmed new model releases only."""
    from .releases import config as rconfig
    from .releases.render import render_models
    from .releases.store import Store

    return render_models(Store().recent_releases(30), rconfig.load()["companies"])


def monitored_sources():
    from .releases import config as rconfig
    from .releases.render import render_sources
    from .releases.store import Store

    return render_sources(rconfig.load(), Store().failures)


def compare_models(args):
    """The /compare reply: two models side by side from the official catalogs."""
    import html

    from .releases import catalog
    from .releases import config as rconfig
    from .releases.render import render_compare
    from .releases.store import Store

    pair = catalog.split_pair(args)
    if not pair:
        return ["Name two models, e.g. <i>/compare Claude Opus 5.5 vs GPT-6.1 Terra</i>"]
    companies = rconfig.load()["companies"]
    index = catalog.model_index(Store(), companies)
    found = [catalog.find(index, q) for q in pair]
    missing = [q for q, m in zip(pair, found) if not m]
    if missing:
        return ["I couldn't find " + " or ".join(f"<b>{html.escape(q)}</b>" for q in missing) +
                " in the official model catalogs of the six labs I monitor. Try the model id, e.g. gpt-6.1-terra."]
    return render_compare(found[0], found[1], companies)


def send_free_models(config):
    send_telegram(freemodels.render(freemodels.collect(config)))


def handle(config):
    """Answer any commands sent to the bot since the last run."""
    token, chat_id = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return
    state = State("telegram")
    memory = state.source("bot")
    api = f"https://api.telegram.org/bot{token}"
    menu = [c["command"] for c in COMMANDS]
    if memory.get("menu") != menu:  # show the commands in Telegram's "/" menu (again when the list changes)
        if requests.post(f"{api}/setMyCommands", json={"commands": COMMANDS}, timeout=30).ok:
            memory["menu"] = menu
            memory.pop("menu_set", None)
    params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
    if memory.get("offset"):
        params["offset"] = memory["offset"] + 1
    updates = requests.get(f"{api}/getUpdates", params=params, timeout=30).json().get("result", [])
    wanted = []
    for u in updates:
        memory["offset"] = max(memory.get("offset", 0), u["update_id"])
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(chat_id):
            continue  # someone else found your bot: ignore them
        raw = (msg.get("text") or "").strip()
        words = raw.split()
        command = words[0].lstrip("/").split("@")[0].lower() if words else ""
        if command in ("ask", "compare"):
            question = raw.split(None, 1)[1].strip() if len(words) > 1 else ""
            wanted.append((command, question))
            continue
        command = command if command in ("free", "latest", "repos", "buzz", "news", "releases", "models", "sources",
                                         "health", "weekly", "weeklymodels", "discoveries") else "help"
        if command not in wanted:
            wanted.append(command)
    print(f"Telegram commands: {', '.join(c if isinstance(c, str) else c[0] for c in wanted) or 'none'}")
    try:
        for command in wanted:
            if command == "free":
                send_free_models(config)
            elif command == "latest":
                if LAST_BRIEF.exists():
                    send_telegram(json.loads(LAST_BRIEF.read_text(encoding="utf-8")))
                else:
                    send_telegram(["No brief has been sent yet. The next one arrives at the next 6-hour run."])
            elif command == "repos":
                if LAST_REPOS.exists():
                    send_telegram(json.loads(LAST_REPOS.read_text(encoding="utf-8")))
                else:
                    send_telegram(["No Builder picks yet. They arrive every morning."])
            elif command == "buzz":
                if LAST_BUZZ.exists():
                    send_telegram(json.loads(LAST_BUZZ.read_text(encoding="utf-8")))
                else:
                    send_telegram(["No Buzz yet. It arrives every 3 hours."])
            elif command == "releases":
                send_telegram(recent_releases())
            elif command == "models":
                send_telegram(recent_models())
            elif command == "sources":
                send_telegram(monitored_sources())
            elif command == "health":
                from . import health
                send_telegram(health.render())
            elif command == "weekly":
                if LAST_WEEKLY.exists():
                    send_telegram(json.loads(LAST_WEEKLY.read_text(encoding="utf-8")))
                else:
                    send_telegram(["No weekly report yet. It arrives on Sunday evening."])
            elif command == "discoveries":
                if LAST_DISCOVERY.exists():
                    send_telegram(json.loads(LAST_DISCOVERY.read_text(encoding="utf-8")))
                else:
                    send_telegram(["No discoveries sent yet. The discovery engine runs every 3 hours."])
            elif command == "weeklymodels":
                if LAST_MODEL_WEEKLY.exists():
                    send_telegram(json.loads(LAST_MODEL_WEEKLY.read_text(encoding="utf-8")))
                else:
                    send_telegram(["No Weekly Model Intelligence Report yet. It arrives on Saturday afternoon."])
            elif isinstance(command, tuple) and command[0] == "compare":
                send_telegram(compare_models(command[1]))
            elif command == "news":
                send_telegram(["/news needs the instant bot (see 'Instant replies' in the README). For now, open GitHub: "
                               "Actions → AI news digest → Run workflow → digest."])
            elif isinstance(command, tuple):  # /ask
                question = command[1]
                if not question:
                    send_telegram(["Type your question after /ask, e.g. <i>/ask what did OpenAI release this week?</i>"])
                else:
                    try:
                        send_telegram([answer_question(config, question)])
                    except Exception as e:  # noqa: BLE001
                        send_telegram([f"Sorry, I couldn't answer that right now ({str(e)[:100]})."])
            else:
                send_telegram([HELP])
    finally:
        state.save()  # remember which messages were answered, even if a reply failed
