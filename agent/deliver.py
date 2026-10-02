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


def send_telegram(messages):
    token, chat_id = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for text in messages:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        r = requests.post(url, data=payload, timeout=30)
        if r.status_code == 400 and "parse" in r.text.lower():
            # Formatting problem: send the same text without HTML rather than nothing.
            payload.pop("parse_mode")
            payload["text"] = re.sub(r"<[^>]+>", "", text)
            r = requests.post(url, data=payload, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Telegram said HTTP {r.status_code}: {r.text[:300]}")


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
