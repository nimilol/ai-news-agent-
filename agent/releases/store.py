"""Persistent memory for model releases: state/releases.json, committed by the workflows like the other state files.

It reuses agent.state.State (per-source memory and failure counts) and keeps four extra sections that are
never pruned by date:

    registry   company -> model id -> {name, status, specs, first_seen, sources}   (official models seen so far)
    history    release id -> the release record (see new_release())
    index      "company:base key" -> release id (every model of a release points at its record)
    outbox     notification id -> a Telegram message waiting for, or confirmed by, Telegram
    health     source name -> {"alerted": bool} (a broken source is reported once per failure streak)
    changes    the last 300 classified changes that did not become releases (docs, pricing, features)
"""

import hashlib

from ..common import now_utc
from ..state import State
from . import identity

MAX_SEEN = 4000
MAX_CHANGES = 300


def now_iso():
    return now_utc().replace(microsecond=0).isoformat()


class Store:
    def __init__(self, name="releases"):
        self.state = State(name)
        x = self.state.extra
        self.registry = x.setdefault("registry", {})
        self.history = x.setdefault("history", {})
        self.index = x.setdefault("index", {})
        self.outbox = x.setdefault("outbox", {})
        self.health = x.setdefault("health", {})
        self.changes = x.setdefault("changes", [])
        self.meta = x.setdefault("meta", {})
        self.known_before = {}        # company -> base keys known before this run's new observations
        self.known_exact_before = {}  # company -> exact keys (with snapshot/variant) known before this run

    # ------------------------------------------------------------------ per-source memory
    def source(self, name):
        return self.state.source(name)

    def is_new_source(self, name):
        return self.state.is_new_source(name)

    def mark_initialized(self, name):
        self.state.mark_initialized(name)

    def remember(self, name, uids):
        mem = self.source(name)
        seen = list(dict.fromkeys(list(uids) + mem.get("seen", [])))
        mem["seen"] = seen[:MAX_SEEN]

    def seen(self, name):
        return set(self.source(name).get("seen", []))

    def record_result(self, name, ok):
        self.state.record_result(name, ok)

    @property
    def failures(self):
        return self.state.failures

    # ------------------------------------------------------------------ registry of official models
    def models(self, company):
        return self.registry.setdefault(company, {})

    def learn_model(self, company, entry, source_name):
        reg = self.models(company)
        mid = entry["id"]
        rec = reg.get(mid)
        if rec is None:
            rec = reg[mid] = {"name": entry.get("name") or mid, "status": entry.get("status", "active"),
                              "specs": {}, "first_seen": now_iso(), "sources": []}
        if entry.get("name") and entry["name"] != mid and (rec["name"] == mid or not rec.get("name")):
            rec["name"] = entry["name"]
        if entry.get("status"):
            rec["status"] = entry["status"]
        rec["specs"] = {**rec.get("specs", {}), **{k: v for k, v in (entry.get("specs") or {}).items() if v}}
        if entry.get("url"):
            rec["url"] = entry["url"]
        if source_name not in rec["sources"]:
            rec["sources"].append(source_name)
        return rec

    def known_bases(self, company, company_cfg=None):
        bases = {identity.base_key(mid, company_cfg) for mid in self.models(company)}
        bases |= {k.split(":", 1)[1] for k, rid in self.index.items()
                  if k.startswith(company + ":") and self.history.get(rid, {}).get("verification")
                  in ("OFFICIAL_CONFIRMED", "OFFICIAL_UPDATE")}
        return {b for b in bases if b}

    def known_exact(self, company, company_cfg=None):
        return {identity.exact_key(mid, company_cfg) for mid in self.models(company)}

    # ------------------------------------------------------------------ release history
    def release_for(self, company, base):
        rid = self.index.get(f"{company}:{base}")
        return self.history.get(rid) if rid else None

    def new_release(self, company, bases, rid=None, **fields):
        """A new history record. Its id is company:base keys (a launch) unless `rid` is given (an update record);
        every base key in `bases` is indexed to it."""
        rid = rid or f"{company}:{'+'.join(sorted(bases))}"
        now = now_iso()
        rec = {
            "id": rid, "company": company, "model_name": "", "model_ids": [], "previous_model": None,
            "classification": "", "verification": "", "announcement_date": None, "availability_date": None,
            "availability_status": None, "source_url": None, "official_sources": [], "fingerprints": [],
            "comparison_summary": "", "details": {}, "first_detected": now, "last_updated": now,
            "delivery": {"status": "NOT_SENT", "message_ids": [], "sent_at": None, "attempts": 0,
                         "last_error": None, "notification": None},
            "events": [], "duplicates": 0,
        }
        rec.update(fields)
        self.history[rid] = rec
        for b in bases:
            self.index[f"{company}:{b}"] = rid
        return rec

    def log_event(self, rec, kind, note=""):
        rec.setdefault("events", []).append({"at": now_iso(), "type": kind, "note": note[:300]})
        rec["events"] = rec["events"][-50:]
        rec["last_updated"] = now_iso()

    def log_change(self, change):
        self.changes.insert(0, {**change, "at": now_iso()})
        del self.changes[MAX_CHANGES:]

    def recent_releases(self, limit=8):
        recs = [r for r in self.history.values() if r.get("verification") != "REJECTED_AS_DUPLICATE"]
        return sorted(recs, key=lambda r: r.get("first_detected") or "", reverse=True)[:limit]

    # ------------------------------------------------------------------ outbox
    def enqueue(self, release_id, kind, category, verification, model_name, parts):
        digest = hashlib.sha1("\n".join(parts).encode()).hexdigest()[:10]
        nid = f"{release_id}|{kind}|{digest}"
        if nid in self.outbox:  # the very same message is already queued or sent: never send it twice
            return nid
        self.outbox[nid] = {"id": nid, "release_id": release_id, "kind": kind, "category": category,
                            "verification": verification, "model_name": model_name, "parts": parts,
                            "sent_parts": {}, "status": "PENDING", "attempts": 0, "created": now_iso(),
                            "sent_at": None, "last_error": None, "log": [],
                            "receipt": {"status": "NOT_NEEDED"}}
        return nid

    def pending(self):
        return sorted((n for n in self.outbox.values() if n["status"] in ("PENDING", "RETRY_PENDING")),
                      key=lambda n: n["created"])

    def save(self):
        # Delivered notifications keep their record in the release history; their full text is dropped from the
        # outbox after 30 days so the file stays small.
        cutoff = now_utc().timestamp() - 30 * 86400
        for nid in list(self.outbox):
            n = self.outbox[nid]
            if n["status"] == "SENT" and n.get("sent_at") and _ts(n["sent_at"]) < cutoff:
                del self.outbox[nid]
        self.state.save()


def _ts(iso):
    from ..common import parse_date

    d = parse_date(iso)
    return d.timestamp() if d else 0
