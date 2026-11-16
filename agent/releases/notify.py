"""Telegram delivery with confirmation, kept separate from release verification.

Every alert goes through the outbox in state/releases.json:

    PENDING        queued, not tried yet (or Telegram isn't set up)
    SENT           Telegram answered "ok": true with a message id for EVERY part of the message
    RETRY_PENDING  a send failed; it is retried on the next run
    FAILED         still failing after `max_delivery_attempts` runs (kept in the history; never deleted)

A long alert is several Telegram messages ("parts"). Each part's message id is saved the moment Telegram
confirms it, and the state file is written right away, so a retry only sends the parts that were not confirmed
and you never get the same part twice. Calling the send function is never taken as proof of delivery: only
Telegram's own response is.

After a confirmed delivery the last part's "DELIVERY: Pending Telegram confirmation." line is edited to show the
confirmed message id (best effort), and a short delivery receipt is sent for the categories in `receipts_for`.
"""

from .. import ledger as led
from .. import messages as msg
from ..deliver import TelegramError, edit_telegram_message, send_telegram_message, telegram_configured
from .render import PENDING_LINE, delivered_line, render_receipt
from .store import now_iso


def _update_release(store, n):
    rec = store.history.get(n["release_id"])
    if not rec or n["kind"] not in ("release", "review", "update", "followup"):
        return
    rec["delivery"] = {"status": n["status"], "message_ids": [n["sent_parts"][k]["message_id"]
                                                                for k in sorted(n["sent_parts"], key=int)],
                       "sent_at": n.get("sent_at"), "attempts": n["attempts"], "last_error": n.get("last_error"),
                       "notification": n["id"]}
    rec["last_updated"] = now_iso()


def _log(n, text):
    n.setdefault("log", []).append(f"{now_iso()} {text}")
    n["log"] = n["log"][-20:]
    print(f"  [{n['model_name']}] {text}")


def send_one(store, n, settings, save):
    """Try to deliver one notification. Returns its new status."""
    if not telegram_configured():
        _log(n, "Telegram is not set up (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID): left PENDING")
        return n["status"]
    max_attempts = int(settings.get("max_delivery_attempts", 6))
    n["attempts"] += 1
    for idx, text in enumerate(n["parts"]):
        key = str(idx)
        if key in n["sent_parts"]:
            continue  # Telegram already confirmed this part on an earlier try
        try:
            confirmation = send_telegram_message(text, silent=msg.is_silent(n.get("priority", "P1")))
        except TelegramError as e:
            n["last_error"] = str(e)[:300]
            n["status"] = "RETRY_PENDING" if n["attempts"] < max_attempts else "FAILED"
            _log(n, f"part {idx + 1}/{len(n['parts'])} not delivered ({n['last_error']}) → {n['status']}")
            _update_release(store, n)
            save()
            return n["status"]
        n["sent_parts"][key] = {"message_id": confirmation["message_id"], "telegram_date": confirmation.get("date"),
                                "at": now_iso()}
        save()  # written before the next part, so a crash can never cause this part to be sent again
    n["status"] = "SENT"
    n["sent_at"] = now_iso()
    n["last_error"] = None
    ids = [n["sent_parts"][k]["message_id"] for k in sorted(n["sent_parts"], key=int)]
    _log(n, f"SENT: Telegram confirmed message id(s) {ids}")
    _update_release(store, n)
    remember_sent(store, n)
    save()
    last = n["parts"][-1]
    if PENDING_LINE in last:
        edit_telegram_message(ids[-1], last.replace(PENDING_LINE, delivered_line(ids, n["sent_at"])))
    if n["category"] in settings.get("receipts_for", []) and n["kind"] in ("release", "update"):
        n["receipt"] = {"status": "PENDING"}
        send_receipt(store, n, save)
    return n["status"]


def remember_sent(store, n):
    """Tell every other run this release was delivered (state/sent.json), so it isn't sent again as an instant
    alert, a digest story or a Buzz pick."""
    rec = store.history.get(n["release_id"])
    if not rec or n["kind"] == "health":
        return
    ledger = led.Ledger()
    keys = [led.url_key(s.get("url")) for s in rec.get("official_sources", []) if s.get("url")]
    keys += [led.title_key(rec["model_name"])]
    keys += [f"model:{rec['company']}:{b.split(':', 1)[-1]}" for b, rid in store.index.items()
             if rid == rec["id"] and b.startswith(rec["company"] + ":")]
    ledger.record(keys, rec["model_name"], "release", n.get("priority"))
    ledger.save()


def send_receipt(store, n, save):
    ids = [n["sent_parts"][k]["message_id"] for k in sorted(n["sent_parts"], key=int)]
    try:
        confirmation = send_telegram_message(render_receipt(n, ids, n["sent_at"]), silent=True)
        n["receipt"] = {"status": "SENT", "message_id": confirmation["message_id"], "at": now_iso()}
        _log(n, f"delivery receipt SENT (message id {confirmation['message_id']})")
    except TelegramError as e:
        n["receipt"] = {"status": "RETRY_PENDING", "last_error": str(e)[:200]}
        _log(n, f"delivery receipt not delivered ({str(e)[:120]}); retried next run")
    rec = store.history.get(n["release_id"])
    if rec and rec.get("delivery", {}).get("notification") == n["id"]:
        rec["delivery"]["receipt"] = n["receipt"]
    save()


def process_outbox(store, settings, save, limit=None):
    """Deliver what is waiting in the outbox (this run's alerts and earlier failures), oldest first and at most
    `limit` per run (the rest wait for the next run). Returns a count per status."""
    summary = {"SENT": 0, "RETRY_PENDING": 0, "FAILED": 0, "PENDING": 0}
    pending = store.pending()
    if limit is not None and len(pending) > limit:
        summary["PENDING"] += len(pending) - limit
        print(f"  {len(pending) - limit} alert(s) wait for the next run (max {limit} per run)")
        pending = pending[:limit]
    for n in pending:
        status = send_one(store, n, settings, save)
        summary[status] = summary.get(status, 0) + 1
    for n in store.outbox.values():  # receipts that failed after their alert was delivered
        if n["status"] == "SENT" and n.get("receipt", {}).get("status") == "RETRY_PENDING" and telegram_configured():
            send_receipt(store, n, save)
    return summary
