"""Sending: Telegram bot and email (Gmail or any SMTP server)."""

import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from .common import env


def telegram_configured():
    return bool(env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"))


def email_configured():
    return bool(env("EMAIL_ADDRESS") and env("EMAIL_APP_PASSWORD"))


class TelegramError(RuntimeError):
    """Telegram did not confirm the request. `retryable` is False for errors that won't fix themselves
    (bad token, chat not found, bot blocked), True for network problems, rate limits and server errors."""

    def __init__(self, message, status=None, retryable=True):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _telegram_call(method, payload):
    """Call the Bot API and return its `result`, but only when Telegram itself reports success
    (HTTP 200 and "ok": true). Anything else raises TelegramError."""
    token = env("TELEGRAM_BOT_TOKEN")
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=payload, timeout=30)
    except requests.RequestException as e:
        raise TelegramError(f"could not reach Telegram ({type(e).__name__})", retryable=True) from e
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 200 and body.get("ok") is True:
        return body.get("result")
    description = body.get("description") or r.text[:300]
    retryable = r.status_code in (429,) or r.status_code >= 500 or r.status_code == 0
    raise TelegramError(f"Telegram said HTTP {r.status_code}: {description}", status=r.status_code, retryable=retryable)


def send_telegram_message(text, parse_mode="HTML"):
    """Send one message and return Telegram's confirmation: {"message_id": int, "date": unix time}.
    A formatting error is retried once as plain text, so the content still arrives."""
    payload = {"chat_id": env("TELEGRAM_CHAT_ID"), "text": text, "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        result = _telegram_call("sendMessage", payload)
    except TelegramError as e:
        if not (parse_mode and e.status == 400 and "parse" in str(e).lower()):
            raise
        payload.pop("parse_mode")
        payload["text"] = re.sub(r"<[^>]+>", "", text)
        result = _telegram_call("sendMessage", payload)
    if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
        raise TelegramError(f"Telegram answered ok but returned no message id: {str(result)[:200]}", retryable=False)
    return {"message_id": result["message_id"], "date": result.get("date")}


def edit_telegram_message(message_id, text, parse_mode="HTML"):
    """Replace the text of a message the bot sent earlier. Returns True when Telegram confirms it."""
    payload = {"chat_id": env("TELEGRAM_CHAT_ID"), "message_id": message_id, "text": text,
               "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        _telegram_call("editMessageText", payload)
        return True
    except TelegramError:
        return False


def send_telegram(messages):
    """Send several messages in order. Returns their message ids; raises on the first failure."""
    return [send_telegram_message(text)["message_id"] for text in messages]


def send_email(subject, html_body, text_body, config):
    sender = env("EMAIL_ADDRESS")
    recipients = [a.strip() for a in (env("EMAIL_TO") or sender).split(",") if a.strip()]
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, f"AI News Agent <{sender}>", ", ".join(recipients)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    d = config["delivery"]
    with smtplib.SMTP_SSL(d.get("smtp_host", "smtp.gmail.com"), int(d.get("smtp_port", 465)), timeout=60) as s:
        s.login(sender, env("EMAIL_APP_PASSWORD"))
        s.sendmail(sender, recipients, msg.as_string())


def deliver(config, telegram_messages=None, email=None, channels=None):
    """Send to every configured channel. Returns (sent_to, errors)."""
    d = config["delivery"]
    channels = channels or [c for c in ("telegram", "email") if d.get(c)]
    sent, errors = [], []
    if "telegram" in channels and telegram_messages and telegram_configured():
        try:
            send_telegram(telegram_messages)
            sent.append("telegram")
        except Exception as e:  # noqa: BLE001
            errors.append(f"telegram: {e}")
    if "email" in channels and email and email_configured():
        try:
            send_email(*email, config)
            sent.append("email")
        except Exception as e:  # noqa: BLE001
            errors.append(f"email: {e}")
    return sent, errors
