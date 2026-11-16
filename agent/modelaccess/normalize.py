"""Catalog listings -> canonical models and access offers.

A *model* is keyed by company/model-slug (e.g. "anthropic/claude-fable-5-2"), found from the catalogs' canonical
ids, so new models appear without any code change. An *offer* is one way to use a model: one provider's API price,
a free tier, a subscription that includes it, or open weights you can run yourself.

Statuses (per offer, and per model = its best confirmed offer):
    FREE           no payment at all and no quota from us: open weights you run on your own hardware
    LIMITED_FREE   free with limits (requests per day/minute, tokens, auto model selection, ...). Never "unlimited".
    FREE_CREDITS   free credits or a trial that runs out
    LOW_COST       paid, blended price at or under settings.low_cost_max_blended per 1M tokens
    PAID_ONLY      paid (including "included in a paid subscription")
    UNVERIFIED     a $0 listing or a claim nobody official confirms (shown, always with this label)

Verification (per offer):
    OFFICIAL    read from the model maker's own pricing page this run (e.g. Gemini API Free tier)
    VERIFIED    a free tier whose published terms were re-checked on the provider's own docs this run
    CATALOG     a listed price in a provider catalog (models.dev mirrors official and provider price lists;
                OpenRouter's is live)
    UNVERIFIED  not confirmed
"""

import math
import re

from .config import alias_map, excluded

STATUS_ORDER = ["FREE", "LIMITED_FREE", "FREE_CREDITS", "LOW_COST", "PAID_ONLY", "UNVERIFIED"]
FREE_STATUSES = ("FREE", "LIMITED_FREE", "FREE_CREDITS")
CONFIRMED = ("OFFICIAL", "VERIFIED", "CATALOG")
ACCESS_TYPES = {
    "official_free_tier": "Official free tier",
    "official_free_app": "Official free app or chat",
    "promo_credits": "Free or promotional credits",
    "free_trial": "Free trial",
    "free_subscription": "Free subscription plan",
    "third_party_free_api": "Third-party free API",
    "coding_platform": "Coding platform",
    "playground": "Playground / arena",
    "open_weights": "Open weights (self-hosted)",
    "education_startup": "Student or startup programme",
    "paid_api": "Paid API",
    "subscription": "Included in a paid subscription",
}
CATEGORIES = ["general", "reasoning", "coding", "multimodal", "image", "video", "audio", "embedding", "agentic", "small"]
TERM_FIELDS = ["registration", "card", "daily_limit", "monthly_limit", "token_quota", "rate_limit", "geography",
               "commercial_use", "permanence", "notes"]


def norm(text):
    t = str(text or "").strip().lower()
    t = re.sub(r"[\s_./:]+", "-", t)
    t = re.sub(r"[^a-z0-9-]", "", t)
    return re.sub(r"-+", "-", t).strip("-")


def blended(price):
    """(3 x input + output) / 4 per 1M tokens; None without both prices."""
    if not price or price.get("in") is None or price.get("out") is None:
        return None
    return round((3 * price["in"] + price["out"]) / 4, 6)


def heavy(price, ratio=3):
    if not price or price.get("in") is None or price.get("out") is None:
        return None
    return round((price["in"] + ratio * price["out"]) / (1 + ratio), 6)


def agent_cost(price):
    """Agents resend long contexts: half the input is usually a cache hit."""
    if not price or price.get("in") is None or price.get("out") is None:
        return None
    cached = price.get("cache") if price.get("cache") is not None else price["in"]
    return round(0.375 * cached + 0.375 * price["in"] + 0.25 * price["out"], 6)


def is_subscription(pid, cfg):
    return any(re.search(p, pid) for p in cfg["providers"]["subscription_patterns"])


def _rule_ok(rule, mid):
    if not rule:
        return True
    m = re.match(r"id ends with (\S+)", rule)
    return bool(m) and str(mid).endswith(m.group(1))


def strip_free(mid):
    return re.sub(r"(:free|-free)$", "", str(mid))


def categories(m):
    name = f"{m.get('id', '')} {m.get('name', '')}".lower()
    mods = m.get("modalities") or {}
    ins, outs = set(mods.get("input") or []), set(mods.get("output") or [])
    kind = m.get("type")
    cats = set()
    if kind in ("embedding", "reranking"):
        cats.add("embedding")
    if kind in ("image",) or "image" in outs:
        cats.add("image")
    if kind == "video" or "video" in outs:
        cats.add("video")
    if kind in ("speech", "transcription", "realtime") or "audio" in outs:
        cats.add("audio")
    if m.get("reasoning"):
        cats.add("reasoning")
    if re.search(r"cod(e|er|ex|ing)|devstral|codestral", name):
        cats.add("coding")
    if ins & {"image", "video", "pdf", "audio"}:
        cats.add("multimodal")
    if m.get("tool_call") and ((m.get("limit") or {}).get("context") or 0) >= 64000:
        cats.add("agentic")
    size = re.search(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)b(?![a-z])", name)
    if (size and float(size.group(1)) <= 15) or re.search(r"\b(nano|tiny|mini|lite|small)\b|flash-lite", name):
        cats.add("small")
    if not cats & {"embedding", "image", "video", "audio"} and ("text" in outs or not outs):
        cats.add("general")
    return sorted(cats, key=CATEGORIES.index)


def _company(author, amap):
    a = str(author or "").lower()
    return amap.get(a, a)


def _clean_name(name):
    name = re.sub(r"^[^:]{2,40}:\s+", "", str(name or "")).replace("(free)", "").strip()
    return name.rsplit("/", 1)[-1] if re.match(r"^[\w.-]+/[\w.-]+$", name) else name


class Builder:
    """Accumulates models and offers from several catalogs; later sources override earlier ones per offer id."""

    def __init__(self, cfg, verified_rules=None):
        self.cfg = cfg
        self.amap = alias_map(cfg)
        self.models = {}
        self.offers = {}
        self.verified_rules = verified_rules or {}  # provider id -> True when its free-tier terms were re-checked
        self.low = float(cfg["settings"]["low_cost_max_blended"])

    def vetted(self, pid):
        prov = self.cfg["providers"]
        return pid in set(prov.get("vetted") or []) or pid in prov["official"] or pid in prov["free_tier"]

    # ------------------------------------------------------------------ models
    def _model(self, mid, listing, official=False):
        rec = self.models.get(mid)
        lim = listing.get("limit") or {}
        mods = listing.get("modalities") or {}
        if rec is None:
            rec = self.models[mid] = {
                "id": mid, "company": mid.split("/", 1)[0], "name": _clean_name(listing.get("name")) or mid.split("/", 1)[1],
                "family": listing.get("family"), "release_date": listing.get("release_date"),
                "open_weights": bool(listing.get("open_weights")), "context": lim.get("context"),
                "max_output": lim.get("output"), "reasoning": bool(listing.get("reasoning")),
                "tool_call": bool(listing.get("tool_call")), "input": sorted(set(mods.get("input") or [])),
                "output": sorted(set(mods.get("output") or [])), "type": listing.get("type"), "official": official,
                "listings": 0,
            }
        else:
            if official and not rec.get("official"):
                rec.update(name=_clean_name(listing.get("name")) or rec["name"], official=True,
                           release_date=listing.get("release_date") or rec.get("release_date"),
                           family=listing.get("family") or rec.get("family"))
            rec["open_weights"] = rec["open_weights"] or bool(listing.get("open_weights"))
            rec["reasoning"] = rec["reasoning"] or bool(listing.get("reasoning"))
            rec["tool_call"] = rec["tool_call"] or bool(listing.get("tool_call"))
            if (lim.get("context") or 0) > (rec.get("context") or 0):
                rec["context"] = lim.get("context")
            rec["input"] = sorted(set(rec["input"]) | set(mods.get("input") or []))
            rec["output"] = sorted(set(rec["output"]) | set(mods.get("output") or []))
            if not rec.get("release_date"):
                rec["release_date"] = listing.get("release_date")
        rec["listings"] += 1
        return rec

    # ------------------------------------------------------------------ offers
    def _offer(self, oid, **fields):
        prev = self.offers.get(oid, {})
        rec = {**prev, **{k: v for k, v in fields.items() if v is not None}, "id": oid}
        rec["blended"] = blended(rec.get("price"))
        self.offers[oid] = rec
        return rec

    def _paid_status(self, price):
        b = blended(price)
        return "LOW_COST" if b is not None and b <= self.low else "PAID_ONLY"

    def _free_terms(self, pid):
        rule = self.cfg["providers"]["free_tier"].get(pid) or {}
        return {k: rule[k] for k in TERM_FIELDS if rule.get(k)}

    # ------------------------------------------------------------------ models.dev
    def add_models_dev(self, data, gemini=None):
        prov = self.cfg["providers"]
        blocked = set(prov.get("blocked") or [])
        for pid, p in data.items():
            if not isinstance(p, dict) or pid in blocked:
                continue
            official_company = prov["official"].get(pid)
            sub = is_subscription(pid, self.cfg)
            free_rule = prov["free_tier"].get(pid)
            for lid, m in (p.get("models") or {}).items():
                if not isinstance(m, dict) or m.get("status") == "deprecated":
                    continue
                if excluded(f"{lid} {m.get('name', '')}", self.cfg):
                    continue
                cost = m.get("cost")
                zero = bool(cost) and cost.get("input") == 0 and cost.get("output") == 0
                canon = m.get("canonical_model_id")
                if canon and "/" in canon and not canon.startswith("~"):
                    author, slug = canon.split("/", 1)
                    company = _company(author, self.amap)
                elif official_company:
                    company, slug = official_company, lid
                elif zero and free_rule and _rule_ok(free_rule.get("rule"), lid):
                    author, _, slug = lid.rpartition("/")
                    company = _company(author, self.amap) if author else "unattributed"
                else:
                    continue
                mid = f"{company}/{norm(strip_free(slug))}"
                official = official_company == company
                self._model(mid, {**m, "id": lid}, official=official)
                if not cost:
                    continue  # no price listed: the model is known, but nothing can be said about access
                price = {"in": cost.get("input"), "out": cost.get("output"), "cache": cost.get("cache_read")}
                base = dict(model=mid, provider=pid, provider_name=p.get("name") or pid, listing=lid,
                            context=(m.get("limit") or {}).get("context"), tool_call=bool(m.get("tool_call")),
                            url=p.get("doc"), source="models.dev")
                if pid == "openrouter":
                    base["url"] = f"https://openrouter.ai/{lid}"
                if sub:
                    self._offer(f"{pid}:{mid}", **base, role="subscription", access="subscription",
                                status="PAID_ONLY", price=None if zero else price, verification="CATALOG",
                                note="Included in the plan" if zero else None)
                    continue
                role = "official" if official else "third_party" if self.vetted(pid) else "reseller"
                if zero:
                    status, ver, note, terms = "UNVERIFIED", "UNVERIFIED", \
                        "Listed at $0 in the models.dev catalog; no confirmed free tier for this provider", None
                    access = "third_party_free_api" if not official else "official_free_tier"
                    if free_rule and _rule_ok(free_rule.get("rule"), lid):
                        status, ver, note = "LIMITED_FREE", "CATALOG", None
                        access = free_rule.get("access_type") or access
                        terms = self._free_terms(pid)
                        if self.verified_rules.get(pid):
                            ver = "VERIFIED"
                        if pid == "google" and gemini is not None:
                            g = gemini.get(lid)
                            if g is not None and not g["free"]:
                                status, ver, note, terms = "UNVERIFIED", "UNVERIFIED", (
                                    "Listed at $0 in the catalog, but Google's pricing page shows no Free tier"), None
                            elif g is None and not m.get("open_weights"):
                                status, ver, note, terms = "UNVERIFIED", "UNVERIFIED", (
                                    "Listed at $0 in the catalog, but not shown as Free tier on Google's pricing page"), None
                    self._offer(f"{pid}:{mid}:free", **base, role=role, access=access, status=status,
                                price={"in": 0, "out": 0, "cache": 0}, verification=ver, note=note, terms=terms)
                else:
                    self._offer(f"{pid}:{mid}", **base, role=role, access="paid_api", status=self._paid_status(price),
                                price=price, verification="CATALOG")

    # ------------------------------------------------------------------ OpenRouter (live)
    def add_openrouter(self, rows):
        free_rule = self.cfg["providers"]["free_tier"].get("openrouter") or {}
        for r in rows:
            rid = str(r.get("id") or "")
            if "/" not in rid or excluded(f"{rid} {r.get('name', '')}", self.cfg):
                continue
            pr = r.get("pricing") or {}
            try:
                pin, pout = float(pr.get("prompt")) * 1e6, float(pr.get("completion")) * 1e6
            except (TypeError, ValueError):
                continue
            if pin < 0 or pout < 0:
                continue  # router pseudo-models ("auto") report -1
            cache = pr.get("input_cache_read")
            author, slug = rid.split("/", 1)
            company = _company(author, self.amap)
            if company in ("openrouter",) or author.startswith("~"):
                continue  # router pseudo-models and "latest" aliases
            mid = f"{company}/{norm(strip_free(slug))}"
            arch = r.get("architecture") or {}
            listing = {"id": rid, "name": r.get("name"), "release_date": None,
                       "limit": {"context": r.get("context_length"),
                                 "output": (r.get("top_provider") or {}).get("max_completion_tokens")},
                       "modalities": {"input": arch.get("input_modalities"), "output": arch.get("output_modalities")},
                       "reasoning": "reasoning" in (r.get("supported_parameters") or []),
                       "tool_call": "tools" in (r.get("supported_parameters") or []),
                       "open_weights": bool(r.get("hugging_face_id"))}
            self._model(mid, listing)
            base = dict(model=mid, provider="openrouter", provider_name="OpenRouter", listing=rid,
                        context=r.get("context_length"), tool_call=listing["tool_call"],
                        url=f"https://openrouter.ai/{rid}", source="OpenRouter API")
            if pin == 0 and pout == 0:
                if _rule_ok(free_rule.get("rule"), rid):
                    self._offer(f"openrouter:{mid}:free", **base, role="third_party", access="third_party_free_api",
                                status="LIMITED_FREE", price={"in": 0, "out": 0, "cache": 0},
                                verification="VERIFIED" if self.verified_rules.get("openrouter") else "CATALOG",
                                terms=self._free_terms("openrouter"), note=None)
                else:
                    self._offer(f"openrouter:{mid}:free", **base, role="third_party", access="third_party_free_api",
                                status="UNVERIFIED", price={"in": 0, "out": 0, "cache": 0}, verification="UNVERIFIED",
                                note="Listed at $0 without the :free tag (often per-request or per-output pricing)")
            else:
                price = {"in": round(pin, 6), "out": round(pout, 6),
                         "cache": round(float(cache) * 1e6, 6) if cache not in (None, "") else None}
                self._offer(f"openrouter:{mid}", **base, role="third_party", access="paid_api",
                            status=self._paid_status(price), price=price, verification="CATALOG")

    # ------------------------------------------------------------------ Gemini API Free tier (official page)
    def add_gemini_free(self, gemini, url):
        rule = self.cfg["providers"]["free_tier"].get("google") or {}
        for code, g in (gemini or {}).items():
            if not g.get("free") or excluded(code, self.cfg):
                continue
            mid = f"google/{norm(code)}"
            rec = self.models.get(mid) or self._model(mid, {"id": code, "name": g["name"]}, official=True)
            terms = self._free_terms("google")
            if g.get("improves_products"):
                terms["notes"] = "Free-tier prompts and responses are used to improve Google's products (pricing page)."
            self._offer(f"google:{mid}:free", model=mid, provider="google", provider_name="Google (Gemini API)",
                        listing=code, role="official", access=rule.get("access_type") or "official_free_tier",
                        status="LIMITED_FREE", price={"in": 0, "out": 0, "cache": 0}, verification="OFFICIAL",
                        context=rec.get("context"), tool_call=rec.get("tool_call"), url=url,
                        source="Gemini API pricing page", terms=terms, note=None)

    def finish(self, keep_paid=5, keep_subscriptions=4):
        """Categories for every model, and a bounded set of offers per model so the database stays small: every
        free, unverified and self-hosted offer, the official API, OpenRouter, the `keep_paid` cheapest other paid
        providers and `keep_subscriptions` subscriptions."""
        for m in self.models.values():
            m["categories"] = categories({**m, "limit": {"context": m.get("context")},
                                          "modalities": {"input": m.get("input"), "output": m.get("output")}})
        by_model = {}
        for o in self.offers.values():
            by_model.setdefault(o["model"], []).append(o)
        kept = {}
        for mid, offers in by_model.items():
            def cheapest(role):
                return sorted((o for o in offers if o.get("access") == "paid_api" and o.get("role") == role
                               and o.get("provider") != "openrouter"),
                              key=lambda o: (o.get("blended") is None, o.get("blended") or 0))
            paid = cheapest("third_party")[:keep_paid] + cheapest("reseller")[:3]
            subs = [o for o in offers if o.get("access") == "subscription"][:keep_subscriptions]
            keep = {o["id"] for o in offers if o.get("access") not in ("paid_api", "subscription")
                    or o.get("role") == "official" or o.get("provider") == "openrouter"}
            keep |= {o["id"] for o in paid} | {o["id"] for o in subs}
            for o in offers:
                if o["id"] in keep:
                    kept[o["id"]] = o
            self.models[mid]["providers_listed"] = len({o["provider"] for o in offers})
        self.offers = kept
        return self.models, self.offers


def open_weights_offer(m):
    """The self-hosting option for an open-weights model (derived when needed, not stored)."""
    slug = m["id"].split("/", 1)[1]
    return {"id": f"open-weights:{m['id']}", "model": m["id"], "provider": "open-weights",
            "provider_name": "Self-hosted (open weights)", "role": "self_hosted", "access": "open_weights",
            "status": "FREE", "price": {"in": 0, "out": 0, "cache": 0}, "blended": 0.0, "verification": "CATALOG",
            "context": m.get("context"), "tool_call": m.get("tool_call"), "active": True,
            "url": f"https://huggingface.co/models?search={slug}", "source": "models.dev (open weights flag)",
            "terms": {"notes": "Weights are published: free to download, but you supply the hardware. "
                               "Check the licence on the model card (commercial use varies)."}}


def model_status(offers):
    """A model's status: its best confirmed offer (subscriptions count as paid); UNVERIFIED only when nothing about
    it is confirmed."""
    confirmed = [o for o in offers if o.get("verification") in CONFIRMED and o.get("active", True)]
    if not confirmed:
        return "UNVERIFIED" if offers else None
    return min((o["status"] for o in confirmed), key=STATUS_ORDER.index)


def log_price(x):
    return math.log2(1 + max(0.0, x or 0.0))
