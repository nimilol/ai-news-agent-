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
    "/help · this message\n\n"
    "For an instant list, open GitHub: Actions → AI news digest → Run workflow → free-models."
)
COMMANDS = [
    {"command": "free", "description": "Free models you can use right now"},
    {"command": "latest", "description": "Resend the latest news brief"},
    {"command": "help", "description": "What I can do"},
]
LAST_BRIEF = ROOT / "state" / "last_brief.json"


def save_last_brief(messages):
    LAST_BRIEF.parent.mkdir(exist_ok=True)
    LAST_BRIEF.write_text(json.dumps(messages, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


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
    if not memory.get("menu_set"):  # show /free, /latest, /help in Telegram's "/" menu
        if requests.post(f"{api}/setMyCommands", json={"commands": COMMANDS}, timeout=30).ok:
            memory["menu_set"] = True
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
        words = (msg.get("text") or "").strip().lower().split()
        command = words[0].lstrip("/").split("@")[0] if words else ""
        command = command if command in ("free", "latest") else "help"
        if command not in wanted:
            wanted.append(command)
    print(f"Telegram commands: {', '.join(wanted) or 'none'}")
    try:
        for command in wanted:
            if command == "free":
                send_free_models(config)
            elif command == "latest":
                if LAST_BRIEF.exists():
                    send_telegram(json.loads(LAST_BRIEF.read_text(encoding="utf-8")))
                else:
                    send_telegram(["No brief has been sent yet. The next one arrives at the next 6-hour run."])
            else:
                send_telegram([HELP])
    finally:
        state.save()  # remember which messages were answered, even if a reply failed
