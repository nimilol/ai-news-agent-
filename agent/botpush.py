"""Keeps the always-on Telegram bot (Cloudflare Worker, see bot/) up to date.

After each run the agent sends the bot what it needs to answer instantly: the latest brief, Builder picks,
the free-model list and this week's briefs (for /ask). The bot's address is in bot/url.txt, written when
the bot is deployed. The password is derived from your Telegram bot token, which both sides already have.
Nothing happens if the bot isn't deployed.
"""

import hashlib

import requests

from .common import ROOT, env

URL_FILE = ROOT / "bot" / "url.txt"


def bot_url():
    if env("BOT_URL"):
        return env("BOT_URL").rstrip("/")
    return URL_FILE.read_text(encoding="utf-8").strip().rstrip("/") if URL_FILE.exists() else None


def secret(purpose):
    return hashlib.sha256(f"{purpose}:{env('TELEGRAM_BOT_TOKEN')}".encode()).hexdigest()


def push(key, value):
    url = bot_url()
    if not url or not env("TELEGRAM_BOT_TOKEN"):
        return False
    try:
        r = requests.post(f"{url}/push", json={"key": key, "value": value},
                          headers={"Authorization": f"Bearer {secret('push')}"}, timeout=30)
        if r.status_code != 200:
            print(f"  Bot update ({key}) failed: HTTP {r.status_code} {r.text[:100]}")
            return False
        print(f"  Bot updated: {key}")
        return True
    except requests.RequestException as e:
        print(f"  Bot update ({key}) failed: {str(e)[:100]}")
        return False
