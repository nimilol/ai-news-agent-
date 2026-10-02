"""The full list of models you can use for free right now (OpenRouter, OpenCode, ...), on request.

The hourly alerts only report models that are *new*; this module answers "what's free today?".
"""

import html
from datetime import datetime, timezone
from urllib.parse import quote

from .common import get, now_utc
from .digest import split_messages
from .sources import _is_free

NEW_DAYS = 14   # models added within this many days get a 🆕
MAX_PER_GROUP = 40


def _context(n):
    if not n:
        return ""
    return f"{round(n / 1_000_000, 1):g}M context" if n >= 1_000_000 else f"{round(n / 1000):g}K context"


def openrouter_free():
    now = now_utc().timestamp()
    models = []
    for m in get("https://openrouter.ai/api/v1/models").json().get("data", []):
        if not _is_free(m.get("pricing", {})):
            continue
        created = m.get("created") or 0
        models.append({
            "name": m.get("name") or m["id"], "id": m["id"], "context": m.get("context_length") or 0,
            "url": f"https://openrouter.ai/{m['id']}", "new": bool(created) and now - created < NEW_DAYS * 86400,
        })
    return models


def models_dev_free(data, provider_id):
    provider = data.get(provider_id) or {}
    today = now_utc().date()
    models = []
    for model_id, m in (provider.get("models") or {}).items():
        cost = (m or {}).get("cost") or {}
        if not (cost.get("input", 1) == 0 and cost.get("output", 1) == 0):
            continue
        new = False
        try:
            released = datetime.strptime(str(m.get("release_date", ""))[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            new = (today - released.date()).days < NEW_DAYS
        except ValueError:
            pass
        models.append({
            "name": m.get("name") or model_id, "id": model_id, "context": (m.get("limit") or {}).get("context") or 0,
            "url": f"https://models.dev/?search={quote(model_id)}", "new": new,
        })
    return provider.get("name", provider_id), models


def collect(config):
    """[(group name, [models] or an error message)]"""
    groups = []
    try:
        groups.append(("OpenRouter", openrouter_free()))
    except Exception as e:  # noqa: BLE001
        groups.append(("OpenRouter", f"could not load the list ({str(e)[:100]})"))
    providers = config.get("free_models", {}).get("models_dev_providers", ["opencode"])
    try:
        data = get("https://models.dev/api.json").json()
        for pid in providers:
            groups.append(models_dev_free(data, pid))
    except Exception as e:  # noqa: BLE001
        groups.append(("OpenCode", f"could not load the list ({str(e)[:100]})"))
    return groups


def render(groups):
    e = html.escape
    blocks = [f"<b>🆓 Free models you can use right now</b>\n<i>{now_utc():%d %b %Y} · 🆕 = added in the last {NEW_DAYS} days</i>"]
    for name, models in groups:
        if isinstance(models, str):
            blocks.append(f"\n<b>{e(name)}</b>: {e(models)}")
            continue
        models = sorted(models, key=lambda m: (not m["new"], -m["context"], m["name"].lower()))
        lines = [f"\n<b>{e(name)}</b> ({len(models)} free)"]
        for m in models[:MAX_PER_GROUP]:
            extra = f" · {_context(m['context'])}" if m["context"] else ""
            lines.append(f"• {'🆕 ' if m['new'] else ''}<a href=\"{e(m['url'], quote=True)}\">{e(m['name'])}</a>{extra}")
        if len(models) > MAX_PER_GROUP:
            lines.append(f"…and {len(models) - MAX_PER_GROUP} more")
        if not models:
            lines.append("none right now")
        blocks.append("\n".join(lines))
    blocks.append("\n<i>Also free with daily limits: Google Gemini (aistudio.google.com) and Groq (console.groq.com). "
                  "Free models are rate-limited and can change without notice.</i>")
    return split_messages(blocks)
