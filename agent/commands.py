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
    "<b>🛰 Your AI news agent</b>\n"
    "I check your messages every hour, so replies can take up to an hour.\n\n"
    "/free · models you can use for free right now (OpenRouter, OpenCode)\n"
    "/latest · resend the most recent news brief\n"
    "/repos · today's Builder picks (trending AI tools and repos)\n"
    "/buzz · the latest Buzz: what's hot on GitHub, Hugging Face, Reddit and YouTube, with interviews\n"
    "/ask your question · ask about this week's news, answered from your briefs\n"
    "/releases · the latest new AI models from Anthropic, OpenAI, Moonshot, Alibaba, xAI and Google\n"
    "/help · this message\n\n"
    "For an instant list, open GitHub: Actions → AI news digest → Run workflow → free-models."
)
COMMANDS = [
    {"command": "free", "description": "Free models you can use right now"},
    {"command": "latest", "description": "Resend the latest news brief"},
    {"command": "repos", "description": "Today's Builder picks"},
    {"command": "buzz", "description": "What's hot: GitHub, Hugging Face, Reddit, YouTube"},
    {"command": "ask", "description": "Ask about this week's news"},
    {"command": "releases", "description": "Latest new AI models (official, verified)"},
    {"command": "news", "description": "Run a fresh news check now"},
    {"command": "help", "description": "What I can do"},
]
LAST_BRIEF = ROOT / "state" / "last_brief.json"
LAST_REPOS = ROOT / "state" / "last_repos.json"
LAST_BUZZ = ROOT / "state" / "last_buzz.json"
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
        if command == "ask":
            question = raw.split(None, 1)[1].strip() if len(words) > 1 else ""
            wanted.append(("ask", question))
            continue
        command = command if command in ("free", "latest", "repos", "buzz", "news", "releases") else "help"
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
            elif command == "news":
                send_telegram(["/news needs the instant bot (see 'Instant replies' in the README). For now, open GitHub: "
                               "Actions → AI news digest → Run workflow → digest."])
            elif isinstance(command, tuple):
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
