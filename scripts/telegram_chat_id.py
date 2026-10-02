"""Find your Telegram chat id.

1. Send any message to your new bot in Telegram.
2. Run:  python scripts/telegram_chat_id.py <your bot token>
"""

import os
import sys

import requests

token = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TELEGRAM_BOT_TOKEN")
if not token:
    sys.exit("Usage: python scripts/telegram_chat_id.py <bot token>")

updates = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()
if not updates.get("ok"):
    sys.exit(f"Telegram error: {updates}")
chats = {}
for u in updates.get("result", []):
    msg = u.get("message") or u.get("channel_post") or {}
    chat = msg.get("chat")
    if chat:
        chats[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")
if not chats:
    sys.exit("No messages found. Send your bot a message in Telegram first, then run this again.")
for chat_id, name in chats.items():
    print(f"TELEGRAM_CHAT_ID = {chat_id}   ({name})")
