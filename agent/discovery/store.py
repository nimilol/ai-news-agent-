"""The discovery history: state/discovery.json, committed by the workflow like every other memory file.

Built on agent.state.State (per-source memory and failure counts); its extra sections are never pruned:

    resources  resource id -> record: name, canonical URL, platform, owner, identifier, categories, first discovered,
               last checked, quality score, priority, licence class, requirements, security notes, status
               (pool | queued | delivered | rejected | expired), delivery (status, Telegram message ids, time),
               last notified, latest version / commit
    aliases    alias key (canonical URL, gh:owner/repo) -> resource id
    searches   search key -> {last_run, results}  (rotation, so API quota isn't spent on the same search twice)
    outbox     Telegram batches waiting for, or confirmed by, Telegram (same format as the release monitor's)
"""

from ..common import now_utc, parse_date
from ..state import State
from . import identity

STATUSES = ("pool", "queued", "delivered", "rejected", "expired")


def now_iso():
    return now_utc().replace(microsecond=0).isoformat()


def older_than(iso, days):
    d = parse_date(iso)
    return d is None or (now_utc() - d).total_seconds() > days * 86400


class DiscoveryStore:
    def __init__(self, name="discovery"):
        self.state = State(name)
        x = self.state.extra
        self.resources = x.setdefault("resources", {})
        self.aliases = x.setdefault("aliases", {})
        self.searches = x.setdefault("searches", {})
        self.outbox = x.setdefault("outbox", {})
        self.meta = x.setdefault("meta", {})
        self.history = {}  # lets the release monitor's Telegram outbox code (notify.send_one) be reused as is
        self.index = {}

    # ---------------------------------------------------------------- identity
    def find(self, candidate):
        """The existing record for this candidate (same id, or any shared alias), or None."""
        rid = identity.resource_id(candidate)
        if rid in self.resources:
            return self.resources[rid]
        for key in identity.alias_keys(candidate):
            if key in self.aliases and self.aliases[key] in self.resources:
                return self.resources[self.aliases[key]]
        return None

    def remember(self, candidate, **fields):
        rid = identity.resource_id(candidate)
        rec = self.resources.get(rid)
        now = now_iso()
        if rec is None:
            rec = self.resources[rid] = {
                "id": rid, "name": candidate.get("name"), "url": identity.canonical_url(candidate.get("url")),
                "platform": candidate["platform"], "kind": candidate.get("kind"), "owner": candidate.get("owner"),
                "identifier": candidate.get("node_id") or candidate.get("platform_id") or candidate.get("name"),
                "first_discovered": now, "status": "pool",
                "delivery": {"status": "NOT_SENT", "message_ids": [], "sent_at": None}, "last_notified": None,
            }
        rec.update(fields)
        rec["last_checked"] = now
        for key in identity.alias_keys(candidate):
            self.aliases[key] = rid
        return rec

    # ---------------------------------------------------------------- queries
    def by_status(self, status):
        return [r for r in self.resources.values() if r.get("status") == status]

    def pending(self):
        return sorted((n for n in self.outbox.values() if n["status"] in ("PENDING", "RETRY_PENDING")),
                      key=lambda n: n["created"])

    def save(self, prune_days=30):
        # Rejected and expired resources are looked at again after `prune_days` anyway, so their records can go;
        # delivered resources are kept forever (they must never be recommended twice).
        for rid in [r for r, rec in self.resources.items() if rec.get("status") in ("rejected", "expired")
                    and older_than(rec.get("last_checked"), prune_days)]:
            del self.resources[rid]
        self.aliases = {k: v for k, v in self.aliases.items() if v in self.resources}
        self.state.extra["aliases"] = self.aliases
        cutoff = now_utc().timestamp() - 30 * 86400
        for nid in list(self.outbox):  # delivered batches keep their record in each resource; the text goes
            n = self.outbox[nid]
            d = parse_date(n.get("sent_at")) if n["status"] == "SENT" else None
            if d and d.timestamp() < cutoff:
                del self.outbox[nid]
        self.state.save()
