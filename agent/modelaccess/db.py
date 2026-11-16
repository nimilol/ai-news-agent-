"""The model access database: state/modelaccess.json, committed by the workflow like every other memory file.

    meta       last refresh, per-source status (ok / error / last good time), verified provider rules, bundle hash
    models     model id -> canonical model (name, company, categories, context, flags, first/last seen, status)
    offers     offer id -> one way to use a model (provider, access type, status, prices, terms, verification,
               first seen, last seen, last verified, active, misses)
    platforms  platform id -> registry entry state (verified date, check result)
    history    price / quota / status change events, oldest first (capped)
    alerts     alert key -> when it was sent (duplicate prevention across runs)
    outbox     Telegram alert batches waiting for, or confirmed by, Telegram (the release monitor's format)
    leads      community leads (always UNVERIFIED unless a confirmed offer matches)

One record per line, so a refresh that changes three prices changes three lines in git. Never overwritten
wholesale: a refresh merges into it, and an offer disappears only after its source loaded fine twice without it.
"""

import json

from .. import state as state_mod
from ..common import now_utc, parse_date
from .normalize import CONFIRMED, FREE_STATUSES, model_status, open_weights_offer



def default_path():
    return state_mod.ROOT / "state" / "modelaccess.json"  # read at call time, so tests can point it elsewhere


def weekly_path():
    return state_mod.ROOT / "state" / "last_modelaccess_weekly.json"

SECTIONS = ["meta", "models", "offers", "platforms", "alerts", "outbox", "leads"]
SOURCE_OF = {"models.dev": "models_dev", "OpenRouter API": "openrouter", "Gemini API pricing page": "gemini"}
TRACKED = ("status", "price", "verification", "terms", "context")


def now_iso():
    return now_utc().replace(microsecond=0).isoformat()


def hours_since(iso):
    d = parse_date(iso)
    return None if d is None else (now_utc() - d).total_seconds() / 3600


class AccessDB:
    def __init__(self, path=None):
        self.path = path or default_path()
        self._original = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        data = json.loads(self._original) if self._original else {}
        self.meta = data.get("meta") or {}
        self.models = data.get("models") or {}
        self.offers = data.get("offers") or {}
        self.platforms = data.get("platforms") or {}
        self.changes = data.get("history") or []  # saved as "history"
        self.alerts = data.get("alerts") or {}
        self.outbox = data.get("outbox") or {}
        self.leads = data.get("leads") or {}
        self.meta.setdefault("sources", {})
        # notify.send_one (the release monitor's Telegram outbox code, reused as is) looks up release records in
        # store.history / store.index; access alerts have none.
        self.history = {}
        self.index = {}

    # ------------------------------------------------------------------ sources
    def source_result(self, name, ok, error=None):
        s = self.meta["sources"].setdefault(name, {})
        s["at"] = now_iso()
        s["ok"] = bool(ok)
        if ok:
            s["last_ok"] = s["at"]
            s.pop("error", None)
            s["failures"] = 0
        else:
            s["error"] = str(error or "")[:200]
            s["failures"] = s.get("failures", 0) + 1

    def source_ok(self, name):
        return bool(self.meta["sources"].get(name, {}).get("ok"))

    # ------------------------------------------------------------------ merge
    def _event(self, events, kind, **fields):
        ev = {"t": now_iso(), "kind": kind, **{k: v for k, v in fields.items() if v is not None}}
        events.append(ev)

    def apply(self, models, offers, loaded_sources):
        """Merge a fresh build into the database. `loaded_sources`: source names that loaded fine this run (only
        their offers can be marked missing). Returns this run's change events (none on the first, baseline run)."""
        baseline = not self.meta.get("bootstrapped")
        now = now_iso()
        events = []
        for mid, m in models.items():
            old = self.models.get(mid)
            rec = {**(old or {}), **m}
            rec["first_seen"] = (old or {}).get("first_seen") or now
            rec["last_seen"] = now[:10]  # a date: the line changes once a day, not every run
            self.models[mid] = rec
            if old is None and not baseline:
                self._event(events, "new_model", model=mid)
        for oid, o in offers.items():
            old = self.offers.get(oid)
            rec = {**o}
            for keep in ("routes", "routes_checked", "via_openrouter"):
                if old and keep in old and keep not in rec:
                    rec[keep] = old[keep]
            rec["first_seen"] = (old or {}).get("first_seen") or now
            rec["last_seen"] = now[:10]  # a date: the line changes once a day, not every run
            rec["active"] = True
            rec["misses"] = 0
            if rec.get("verification") in CONFIRMED:
                rec["last_verified"] = now[:10]
            else:
                rec["last_verified"] = (old or {}).get("last_verified")
            if old is None:
                if not baseline:
                    self._event(events, "new_offer", offer=oid, model=o["model"], new=_snap(rec))
            else:
                if not old.get("active", True) and not baseline:
                    self._event(events, "offer_returned", offer=oid, model=o["model"], old=_snap(old), new=_snap(rec))
                for field in TRACKED:
                    if _norm(old.get(field)) != _norm(rec.get(field)) and not baseline:
                        self._event(events, "changed", offer=oid, model=o["model"], field=field,
                                    old=old.get(field), new=rec.get(field))
            self.offers[oid] = rec
        for oid, old in self.offers.items():
            if oid in offers or not old.get("active", True):
                continue
            if SOURCE_OF.get(old.get("source")) not in loaded_sources:
                continue  # its catalog didn't load this run: nothing can be concluded
            old["misses"] = old.get("misses", 0) + 1
            if old["misses"] >= 2:
                old["active"] = False
                old["ended"] = now
                if not baseline:
                    self._event(events, "offer_removed", offer=oid, model=old["model"], old=_snap(old))
        self.refresh_statuses(events, baseline)
        self.meta["bootstrapped"] = True
        self.meta["last_refresh"] = now
        self.changes.extend(events)
        return events

    def refresh_statuses(self, events=None, baseline=False):
        by_model = self.offers_by_model()
        for mid, m in self.models.items():
            offers = by_model.get(mid, []) + ([open_weights_offer(m)] if m.get("open_weights") else [])
            status = model_status(offers)
            if status != m.get("status"):
                if events is not None and not baseline and m.get("status"):
                    self._event(events, "model_status", model=mid, old=m.get("status"), new=status)
                m["status"] = status
                m["status_since"] = now_iso()

    # ------------------------------------------------------------------ queries
    def offers_by_model(self, active_only=True):
        out = {}
        for o in self.offers.values():
            if active_only and not o.get("active", True):
                continue
            out.setdefault(o["model"], []).append(o)
        return out

    def free_offers(self, confirmed=True):
        return [o for o in self.offers.values() if o.get("active", True) and o["status"] in FREE_STATUSES
                and (not confirmed or o.get("verification") in CONFIRMED)]

    def events_since(self, hours):
        return [e for e in self.changes if (hours_since(e.get("t")) or 1e9) <= hours]

    def pending(self):
        return sorted((n for n in self.outbox.values() if n["status"] in ("PENDING", "RETRY_PENDING")),
                      key=lambda n: n["created"])

    def age_hours(self):
        return hours_since(self.meta.get("last_refresh"))

    # ------------------------------------------------------------------ save
    def save(self, history_keep=3000, leads_keep_days=45, alerts_keep_days=120):
        if len(self.changes) > history_keep:
            del self.changes[: len(self.changes) - history_keep]
        self.leads = {k: v for k, v in self.leads.items() if (hours_since(v.get("first_seen")) or 0) <= leads_keep_days * 24}
        self.alerts = {k: v for k, v in self.alerts.items() if (hours_since(v.get("at")) or 0) <= alerts_keep_days * 24}
        for k, n in list(self.outbox.items()):
            if n["status"] == "SENT" and (hours_since(n.get("sent_at")) or 0) > 30 * 24:
                del self.outbox[k]
        lines = ["{"]
        parts = []
        for name in SECTIONS:
            value = getattr(self, name)
            if isinstance(value, dict) and name != "meta" and value:
                body = ",\n".join(f"  {json.dumps(k, ensure_ascii=False)}: "
                                  f"{json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
                                  for k, v in sorted(value.items()))
                parts.append(f' "{name}": {{\n{body}\n }}')
            else:
                parts.append(f' "{name}": {json.dumps(value, ensure_ascii=False, sort_keys=True)}')
        hist = ",\n".join("  " + json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                          for e in self.changes)
        parts.append(f' "history": [\n{hist}\n ]' if hist else ' "history": []')
        lines.append(",\n".join(parts))
        lines.append("}")
        text = "\n".join(lines) + "\n"
        json.loads(text)  # never write a file that can't be read back
        if text != self._original:
            self.path.parent.mkdir(exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.path)
            self._original = text


def _snap(o):
    return {k: o.get(k) for k in ("status", "price", "provider_name", "verification", "terms") if o.get(k) is not None}


def _norm(v):
    return json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v
