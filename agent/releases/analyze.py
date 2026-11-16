"""Reads a confirmed release's official sources and builds the facts for the alert: name, dates, availability,
what's new, the previous model and an evidence-only comparison.

Deterministic first: dates come from the sources' own dates, specs (context, output limit, prices, latency,
modalities) from the official model pages, and the predecessor from the company's own wording ("an upgrade over
Claude Sonnet 5", "migration from Gemini 3.8 Flash") or else from the model lineage in the official catalog.

An AI model (agent/llm.py, the same free-provider chain as the briefs) then reads the official texts for the
things only prose contains: benchmark scores, qualitative claims, why it matters. Every AI item must carry a
verbatim quote that really appears in the cited official source, and every number it states must appear in the
sources; anything else is thrown away. Without any AI the alert still goes out with the deterministic facts.
"""

import json
import re

from .. import accuracy, llm
from ..common import clean_text
from . import fetchers, identity
from .classify import A, B, C, CONFIRMED, REVIEW

COMPARE_ROWS = [
    ("architecture", "🏗 Architecture / family"),
    ("reasoning", "🧠 Reasoning"),
    ("coding", "💻 Coding"),
    ("math", "➗ Math & problem-solving"),
    ("multimodal", "🖼 Multimodal"),
    ("context", "📏 Context window"),
    ("output", "📤 Output limit"),
    ("tools", "🛠 Tool use & agents"),
    ("speed", "⚡ Speed & latency"),
    ("pricing", "💰 API pricing"),
    ("availability", "🌍 Availability"),
]
CUE = re.compile(r"(?:upgrade (?:over|from|to)|successor (?:to|of)|replac(?:es|ing|ement for)|compared (?:to|with)|"
                 r"\bthan\b|\bvs\.?|\bversus\b|\bover\b|\bfrom\b|previous[- ]generation|predecessor|"
                 r"migrat\w* from|builds? on|building on)\s+(?:the\s+|our\s+)?$", re.I)

RELEASE_PROMPT = """You analyse official AI model announcements for a creator who builds with AI and teaches people
about it. You get numbered OFFICIAL sources from one company (announcement posts, API release notes, model
documentation) about a possible new model, plus the official specs the agent already parsed for the new model and
for its likely predecessor.

Use ONLY these sources. Never use outside knowledge, never guess, never invent numbers or benchmark scores.
Every factual item must include "source" (the source number) and "quote": a verbatim fragment copied exactly from
that source (10-200 characters) that supports it. Items without a verbatim quote are discarded.

Answer with JSON only:
{"model_name": "official name",
 "is_new_model": "yes | no | unclear",
 "classification_quote": {"source": 1, "quote": ""},
 "announcement_date": {"value": "YYYY-MM-DD or empty", "source": 1, "quote": ""},
 "availability_date": {"value": "YYYY-MM-DD or empty", "source": 1, "quote": ""},
 "availability_status": {"value": "generally available | preview | limited | rolling out | ...", "source": 1, "quote": ""},
 "api_access": {"value": "how developers get it", "source": 1, "quote": ""},
 "predecessor": {"name": "the previous model the company itself compares with or replaces, or empty", "source": 1, "quote": ""},
 "whats_new": [{"text": "one short sentence", "source": 1, "quote": ""}],
 "comparison": {"<architecture|reasoning|coding|math|multimodal|tools|speed|availability>":
                {"new": "", "previous": "", "source": 1, "quote": ""}},
 "benchmarks": [{"name": "", "new": "", "previous": "", "source": 1, "quote": ""}],
 "claims": [{"text": "a qualitative claim the company makes", "source": 1, "quote": ""}],
 "interpretation": ["at most 3 short sentences on what meaningfully improved, using only the facts above"],
 "why_it_matters": "2 sentences for someone who builds with AI and teaches people about it",
 "explain": {"introduced": "what was introduced", "does": "what it actually does", "problem": "what problem it solves",
             "different": "how it differs from what existed before", "who": "who benefits",
             "care": "why an AI creator or developer should care"},
 "content": [{"kind": "news", "title": "", "angle": "", "why": "", "research": ""},
             {"kind": "education", "title": "", "angle": "", "why": "", "research": ""},
             {"kind": "analysis", "title": "", "angle": "", "why": "", "research": ""}]}

Rules:
- "is_new_model" is "yes" only when the sources announce a new model or model generation, not a new snapshot,
  alias, feature, price change or availability change of an existing model.
- benchmarks: only scores printed in the sources, copied exactly, with the benchmark's name. Fill "previous" only
  when the sources give the previous model's score on the same benchmark.
- comparison "previous": only what the sources say about the previous model. Leave it empty otherwise.
- explain: plain language with accurate technical terms, only from the sources; don't oversimplify into something
  inaccurate. Leave a question empty when the sources don't answer it.
- content: "news" = a timely video on what happened, "education" = a video explaining the technology,
  "analysis" = a comparison, practical test or opinion. Each: a title, the main angle, why it is interesting, and
  what still needs to be researched or tested before making it (e.g. claims to verify, tests to run).
- Leave any field empty rather than guessing."""


# ----------------------------------------------------------------------------------------------- evidence checks

def _norm(text):
    text = (text or "").lower()
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "}))
    return re.sub(r"\s+", " ", text).strip()


def quote_supported(quote, source_text):
    """True when the quote (or every '...'-separated fragment of it, 12+ characters) appears in the source."""
    q, src = _norm(quote), _norm(source_text)
    if len(q) < 10 or not src:
        return False
    parts = [p.strip(" .,;:") for p in re.split(r"\.\.\.|…", q) if p.strip(" .,;:")]
    return all(len(p) >= 12 and p in src for p in parts) if len(parts) > 1 else q.strip(" .,;:") in src


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_supported(text, corpus):
    """Every number written in `text` also appears somewhere in the official sources or parsed specs."""
    c = _norm(corpus).replace(",", "")
    for n in _NUMBER.findall(text or ""):
        n = n.replace(",", "")
        if n not in c:
            return False
    return True


def _item_ok(item, sources, corpus):
    if not isinstance(item, dict):
        return False
    try:
        src = sources[int(item.get("source", 0)) - 1]
    except (ValueError, IndexError, TypeError):
        return False
    if not quote_supported(str(item.get("quote", "")), src["text"]):
        return False
    values = " ".join(str(v) for k, v in item.items() if k not in ("source", "quote"))
    return numbers_supported(values, corpus)


def _parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[start:end + 1])


def validate_ai(data, sources, corpus):
    """Keep only AI output backed by verbatim quotes and supported numbers. Returns (clean dict, dropped count)."""
    if not isinstance(data, dict):
        return {}, 0
    out, dropped = {}, 0
    for key in ("announcement_date", "availability_date", "availability_status", "api_access", "predecessor",
                "classification_quote"):
        item = data.get(key)
        if isinstance(item, dict) and (item.get("value") or item.get("name") or key == "classification_quote"):
            if _item_ok(item, sources, corpus):
                out[key] = item
            else:
                dropped += 1
    for key in ("whats_new", "benchmarks", "claims"):
        kept = []
        for item in data.get(key) or []:
            if _item_ok(item, sources, corpus):
                kept.append(item)
            else:
                dropped += 1
        out[key] = kept[:6]
    comp = {}
    for k, item in (data.get("comparison") or {}).items():
        if k in dict(COMPARE_ROWS) and isinstance(item, dict) and (item.get("new") or item.get("previous")):
            if _item_ok(item, sources, corpus):
                comp[k] = item
            else:
                dropped += 1
    out["comparison"] = comp
    for key in ("interpretation",):
        lines = [clean_text(str(x), 300) for x in (data.get(key) or [])[:3]]
        kept = [x for x in lines if x and numbers_supported(x, corpus)]
        dropped += len(lines) - len(kept)
        out[key] = kept
    why = clean_text(str(data.get("why_it_matters") or ""), 500)
    out["why_it_matters"] = why if numbers_supported(why, corpus) else ""
    from .. import messages as msg

    check = lambda v: numbers_supported(v, corpus)  # noqa: E731
    out["explain"] = msg.clean_explain(data.get("explain"), check)
    out["content"] = msg.clean_opportunities(data.get("content"), check)
    verdict = str(data.get("is_new_model") or "").lower().strip()
    out["is_new_model"] = verdict if verdict in ("yes", "no", "unclear") else ""
    if out["is_new_model"] == "no" and "classification_quote" not in out:
        out["is_new_model"] = "unclear"  # a "no" only counts with evidence
    out["model_name"] = clean_text(str(data.get("model_name") or ""), 80)
    return out, dropped


# ----------------------------------------------------------------------------------------------- predecessor

def _registry_bases(store, company, cfg, exclude):
    """base key -> representative registry entry (prefers a plain id over snapshots/variants)."""
    out = {}
    for mid, rec in store.models(company).items():
        p = identity.parse(mid, cfg)
        b = identity.base_key(p)
        if not b or b in exclude:
            continue
        plain = not p["snapshot"] and not p["variants"]
        if b not in out or (plain and not out[b]["plain"]):
            name = rec.get("name") if rec.get("name") and rec.get("name") != mid else identity.display_name(mid)
            out[b] = {"id": mid, "name": name, "parsed": p, "plain": plain, "rec": rec}
    return out


def find_predecessor(company, cfg, new_ids, texts, store, ai_name=None):
    """The previous model, with how it was found:
       1. the company's own wording in the official sources ("an upgrade over X", "migration from X", "than X")
       2. the AI's reading, accepted only if that model is in the official catalog or named in the sources
       3. lineage: same family and tier, highest earlier version (decimal-style: 4.20 < 4.3)
       4. same family, any tier, highest earlier version (lower confidence)"""
    new_parsed = [identity.parse(m, cfg) for m in new_ids]
    new_bases = {identity.base_key(p) for p in new_parsed}
    main = new_parsed[0] if new_parsed else None
    reg = _registry_bases(store, company, cfg, new_bases)
    patterns = cfg.get("_patterns") or []

    def entry_for(base, name):
        if base in reg:
            return {"id": reg[base]["id"], "name": reg[base]["name"], "base": base}
        return {"id": None, "name": identity.display_name(name), "base": base}

    votes = {}
    for t in texts:
        body = t["text"]
        for pat in patterns:
            for m in pat.finditer(body):
                base = identity.base_key(m.group(0), cfg)
                if not base or base in new_bases:
                    continue
                before = body[max(0, m.start() - 40):m.start()]
                if CUE.search(before):
                    votes.setdefault(base, {"count": 0, "name": m.group(0), "quote": body[max(0, m.start() - 60):m.end() + 20]})
                    votes[base]["count"] += 1
    if votes and main:
        def rank(item):
            base, v = item
            p = identity.parse(v["name"], cfg)
            same_family = p["family"] == main["family"]
            return (same_family, v["count"])
        base, v = max(votes.items(), key=rank)
        return {**entry_for(base, v["name"]), "method": "named by the company in the announcement",
                "evidence": clean_text(v["quote"], 160)}

    if ai_name and main:
        base = identity.base_key(ai_name, cfg)
        mentioned = any(base == identity.base_key(x, cfg) for t in texts for x in identity.extract_models(t["text"], patterns))
        if base and base not in new_bases and (base in reg or mentioned):
            return {**entry_for(base, ai_name), "method": "named in the official sources (read by AI, quote checked)",
                    "evidence": ""}

    if not main or not main["version"]:
        return None
    new_v = identity.version_value(main["version"])

    def earlier(same_tier):
        best = None
        for b, e in reg.items():
            p = e["parsed"]
            if p["family"] != main["family"] or (same_tier and p["tier"] != main["tier"]):
                continue
            v = identity.version_value(p["version"])
            if v is None or new_v is None or v >= new_v:
                continue
            if best is None or v > best[0]:
                best = (v, b, e)
        return best

    best = earlier(True)
    if best:
        return {"id": best[2]["id"], "name": best[2]["name"], "base": best[1],
                "method": "model lineage: same family and tier, previous version in the official catalog",
                "evidence": ""}
    best = earlier(False)
    if best:
        return {"id": best[2]["id"], "name": best[2]["name"], "base": best[1],
                "method": "model lineage: same family, previous version (different tier, lower confidence)",
                "evidence": ""}
    return None


# ----------------------------------------------------------------------------------------------- specs & comparison

def _fmt_tokens(n):
    """1000000 → '1M tokens', 1050000 → '1.05M tokens', 128000 → '128K tokens', 1048576 → '1,048,576 tokens'."""
    if not n:
        return None
    if n % 1_000_000 == 0:
        return f"{n // 1_000_000}M tokens"
    if n >= 1_000_000 and n % 10_000 == 0:
        return f"{n / 1_000_000:g}M tokens"
    if n % 1000 == 0:
        return f"{n // 1000}K tokens"
    return f"{n:,} tokens"


def money(x):
    """4 → '$4', 1.5 → '$1.50', 0.1 → '$0.10'."""
    return f"${x:g}" if float(x).is_integer() else f"${x:,.2f}"


def _fmt_price(specs):
    i, o = specs.get("input_price"), specs.get("output_price")
    if i is None and o is None:
        return specs.get("price_note")
    parts = []
    if i is not None:
        parts.append(f"{money(i)} input")
    if o is not None:
        parts.append(f"{money(o)} output")
    return " / ".join(parts) + " per 1M tokens"


def spec_cells(specs, status=None):
    """The comparison rows the official specs can fill on their own."""
    s = specs or {}
    modal = " · ".join(f"{k}: {v}" for k, v in (("in", s.get("input_modalities")), ("out", s.get("output_modalities")))
                       if v)
    cells = {
        "context": _fmt_tokens(s.get("context_tokens")),
        "output": _fmt_tokens(s.get("max_output_tokens")),
        "pricing": _fmt_price(s),
        "speed": f"{s['latency']} (official latency label)" if s.get("latency") else None,
        "reasoning": s.get("reasoning"),
        "multimodal": modal or None,
        "availability": {"active": "listed as available", "limited": "limited availability", "preview": "preview",
                         "restricted": "restricted to existing users", "deprecated": "deprecated"}.get(status or ""),
    }
    return {k: v for k, v in cells.items() if v}


def _pct(old, new):
    if not old:
        return None
    return round((new - old) / old * 100)


def spec_diffs(prev, new):
    """Plain-language differences between two spec dicts: (improvements, regressions, neutral)."""
    better, worse, same = [], [], []
    for key, label in (("context_tokens", "Context window"), ("max_output_tokens", "Output limit")):
        a, b = prev.get(key), new.get(key)
        if a and b:
            if b > a:
                better.append(f"{label} grows from {_fmt_tokens(a)} to {_fmt_tokens(b)}")
            elif b < a:
                worse.append(f"{label} shrinks from {_fmt_tokens(a)} to {_fmt_tokens(b)}")
            else:
                same.append(f"{label} unchanged ({_fmt_tokens(b)})")
    for key, label in (("input_price", "Input price"), ("output_price", "Output price")):
        a, b = prev.get(key), new.get(key)
        if a is not None and b is not None:
            pct = _pct(a, b)
            if b < a:
                better.append(f"{label} drops from {money(a)} to {money(b)} per 1M tokens ({pct}%)")
            elif b > a:
                worse.append(f"{label} rises from {money(a)} to {money(b)} per 1M tokens (+{pct}%)")
            else:
                same.append(f"{label} unchanged ({money(b)} per 1M tokens)")
    if prev.get("latency") and new.get("latency") and prev["latency"] != new["latency"]:
        same.append(f"Latency label changes from {prev['latency']} to {new['latency']}")
    pi, ni = _modal_set(prev.get("input_modalities")), _modal_set(new.get("input_modalities"))
    if pi and ni and ni - pi:
        better.append("New input types: " + ", ".join(sorted(ni - pi)))
    if pi and ni and pi - ni:
        worse.append("Input types no longer listed: " + ", ".join(sorted(pi - ni)))
    return better, worse, same


def _modal_set(text):
    return {w.strip().lower() for w in re.split(r",| and ", text or "") if w.strip()}


def build_comparison(prev_specs, new_specs, prev_status, new_status, ai):
    """{row: {"previous": str|None, "new": str|None, "basis": "official specs"|"company statement", "quote": ...}}"""
    prev_cells, new_cells = spec_cells(prev_specs, prev_status), spec_cells(new_specs, new_status)
    rows = {}
    for key, _ in COMPARE_ROWS:
        cell = {"previous": prev_cells.get(key), "new": new_cells.get(key), "basis": "official specs", "quote": ""}
        claim = (ai.get("comparison") or {}).get(key)
        if claim:
            cell["previous"] = cell["previous"] or (claim.get("previous") or None)
            if claim.get("new"):
                cell["new"] = f"{cell['new']} — {claim['new']}" if cell["new"] else claim["new"]
            cell["basis"] = "official specs + company statement" if (prev_cells.get(key) or new_cells.get(key)) \
                else "company statement"
            cell["quote"] = claim.get("quote", "")
        if cell["previous"] or cell["new"]:
            rows[key] = cell
    return rows


# ----------------------------------------------------------------------------------------------- the analysis

def _best_spec_source(company_cfg, mid):
    """The catalog source whose detail page can describe `mid` (first one with a detail_url)."""
    return next((s for s in company_cfg["sources"] if s.get("role") == "catalog" and s.get("detail_url")), None)


def gather_texts(group_obs, company_cfg, article_chars):
    """The official texts behind a release, read in full. Each: {"url", "source", "kind", "text", "date", "title"}."""
    texts, seen = [], set()
    for o in group_obs:
        fp = o["fingerprints"][0] if o.get("fingerprints") else o["uid"]
        if fp in seen:
            continue
        seen.add(fp)
        entry = {"url": o.get("url"), "source": o["source"], "kind": o["event"], "title": o.get("title", ""),
                 "text": o.get("text", ""), "date": o.get("date")}
        if o["event"] == "announcement" and o.get("url"):
            try:
                body, published, title = accuracy.read_article(o["url"], article_chars, timeout=20)
                if len(body) > 200:
                    entry["text"] = body
                    entry["read"] = True
                entry["date"] = entry["date"] or published
                entry["title"] = entry["title"] or title
            except Exception as e:  # noqa: BLE001
                entry["read_error"] = str(e)[:120]
                print(f"    could not read {o['url']}: {str(e)[:100]}")
        elif o["event"] == "repo_created" and "huggingface.co" in (o.get("url") or ""):
            card = fetchers.hf_model_card(o["uid"].split(":", 1)[-1], article_chars // 2)
            if card:
                entry["text"] = card
                entry["read"] = True
        texts.append(entry)
    return texts


BOILERPLATE = re.compile(r"\b(below|system card|learn more|read more|click|sign up|subscribe|cookie|see (?:the|our)|"
                         r"blog post|full details|this post|we describe)\b", re.I)
SUBSTANCE = re.compile(r"\b(improv\w*|faster|new|support\w*|built for|designed for|context|cheaper|better|lead\w*|"
                       r"state-of-the-art|most capable|available|introduc\w*|launch\w*|price|priced|speed|"
                       r"reasoning|coding|vision|agent\w*|benchmark|open[- ]weight)\b", re.I)


def _first_sentences(text, model_words, n=2):
    """Sentences that say something concrete about the model (deterministic "what's new" without AI)."""
    sentences = re.split(r"(?<=[.!?])\s+", clean_text(text or ""))
    good = [s for s in sentences if 30 < len(s) < 320 and not BOILERPLATE.search(s)]
    good.sort(key=lambda s: not SUBSTANCE.search(s))  # concrete sentences first, but keep the rest as fallback
    picked = [s for s in good if any(w.lower() in s.lower() for w in model_words)]
    return (picked or good)[:n]


def ledger_similar(a, b):
    from ..ledger import similar

    return similar(a, b, 0.7)


def _dedupe_reasons(reasons):
    out = []
    for r in reasons:
        if r not in out:
            out.append(r)
    return out


def analyze(results, store, company_cfg, config, settings, llm_budget):
    """Build the release facts for one launch (a merged group of classification results)."""
    company = results[0]["company"]
    official = [o for r in results for o in r["official"]]
    new_ids, bases = [], []
    for r in results:
        bases.append(r["base"])
        ids = [m for o in r["official"] for m in o.get("model_ids", []) if identity.base_key(m, company_cfg) == r["base"]]
        reg_ids = [m for m in store.models(company) if identity.base_key(m, company_cfg) == r["base"]]
        cand = [m for m in ids + reg_ids if re.fullmatch(r"[a-z0-9.\-/]+", m)] or ids or [r["base"]]
        plain = sorted(cand, key=lambda m: (bool(identity.parse(m, company_cfg)["snapshot"] or
                                                identity.parse(m, company_cfg)["variants"]), len(m)))
        new_ids.append(plain[0].split("/", 1)[-1])
    texts = gather_texts(official, company_cfg, settings.get("article_chars", 12000))
    unreadable = [t for t in texts if t["kind"] == "announcement" and not t.get("read")]

    # Specs from the official catalogs, plus the per-model page for the new model(s).
    specs = {}
    for mid in new_ids:
        rec = store.models(company).get(mid) or {}
        s = dict(rec.get("specs") or {})
        for o in official:
            if mid in o.get("model_ids", []) and o.get("specs"):
                s = {**o["specs"], **s}
        src = _best_spec_source(company_cfg, mid)
        if src:
            s = {**s, **fetchers.fetch_detail(src, mid)}
        specs[mid] = {"specs": s, "status": rec.get("status") or next(
            (o.get("status") for o in official if mid in o.get("model_ids", []) and o.get("status")), None),
            "name": rec.get("name") if rec.get("name") and rec.get("name") != mid else None}

    for mid in new_ids[:1]:
        if specs[mid]["specs"].get("input_price") is None:
            specs[mid]["specs"].update(prices_from_text(texts))

    # AI reading (optional, limited per run).
    ai, ai_model, ai_dropped = {}, None, 0
    prev = find_predecessor(company, company_cfg, new_ids, texts, store)
    if llm_budget["left"] > 0 and any(t["text"] for t in texts):
        llm_budget["left"] -= 1
        prev_specs = (store.models(company).get(prev["id"]) or {}).get("specs", {}) if prev and prev.get("id") else {}
        corpus = " ".join(t["text"] for t in texts) + " " + json.dumps(specs) + " " + json.dumps(prev_specs)

        def build(max_items=None, summary_chars=None, **_):
            limit = 3500 if max_items else settings.get("article_chars", 12000)
            parts = [f"Company: {company_cfg['label']} ({company_cfg['product']})",
                     f"Candidate model id(s): {', '.join(new_ids)}",
                     f"Official specs of the new model(s): {json.dumps({k: v['specs'] for k, v in specs.items()})}",
                     f"Likely previous model: {prev['name'] if prev else 'unknown'}; its official specs: {json.dumps(prev_specs)}",
                     ""]
            for n, t in enumerate(texts, 1):
                parts.append(f"[Source {n}] {t['source']} ({t['kind']}, {t.get('date') or 'date not given'}) "
                             f"{t.get('url') or ''}\nTitle: {t.get('title', '')}\n{t['text'][:limit]}\n")
            return "\n".join(parts)

        try:
            answer, ai_model = llm.complete(config, RELEASE_PROMPT, build)
            ai, ai_dropped = validate_ai(_parse_json(answer), texts, corpus)
        except Exception as e:  # noqa: BLE001 - the alert goes out with deterministic facts only
            print(f"    AI reading skipped: {str(e)[:200]}")
            ai, ai_model = {}, None
        if ai.get("predecessor", {}).get("name"):
            better = find_predecessor(company, company_cfg, new_ids, texts, store, ai["predecessor"]["name"])
            if better and (not prev or prev["method"].startswith("model lineage")) and \
                    better["method"].startswith("named in the official sources"):
                prev = better

    prev_rec = (store.models(company).get(prev["id"]) or {}) if prev and prev.get("id") else {}
    prev_specs = dict(prev_rec.get("specs") or {})
    if prev and prev.get("id"):
        src = _best_spec_source(company_cfg, prev["id"])
        if src:
            prev_specs = {**prev_specs, **fetchers.fetch_detail(src, prev["id"])}
    main_id = new_ids[0]
    comparison = build_comparison(prev_specs, specs[main_id]["specs"], prev_rec.get("status"),
                                  specs[main_id]["status"], ai) if prev else {}
    better, worse, same = spec_diffs(prev_specs, specs[main_id]["specs"]) if prev else ([], [], [])

    # Dates: the announcement date is the earliest official post/changelog date; availability needs evidence.
    ann_dates = sorted(d for d in (t.get("date") for t in texts if t["kind"] in ("announcement", "changelog"))
                       if d and len(d) >= 7)
    announcement_date = ann_dates[0] if ann_dates else None
    if not announcement_date and ai.get("announcement_date", {}).get("value"):
        announcement_date = ai["announcement_date"]["value"]
    avail_obs = [o for o in official if o["event"] == "changelog" and (
        "availability" in o.get("tags", []) or "release" in o.get("tags", []) or "feature_tag" in o.get("tags", []))]
    availability_date = min((o["date"] for o in avail_obs if o.get("date")), default=None)
    if ai.get("availability_date", {}).get("value"):
        availability_date = availability_date or ai["availability_date"]["value"]
    status = specs[main_id]["status"]
    availability_status = (ai.get("availability_status") or {}).get("value") or {
        "active": "Available (listed in the official model catalog)", "limited": "Limited availability",
        "preview": "Preview", "restricted": "Restricted"}.get(status or "", "Not stated in the official sources")
    catalog_hit = [o for o in official if o["event"] == "listed"]
    api_access = (ai.get("api_access") or {}).get("value") or (
        f"Yes: listed in {catalog_hit[0]['source']} as " + ", ".join(f"`{m}`" for m in new_ids)
        if catalog_hit else "Not stated in the official sources")

    names = [specs[m]["name"] or (official[0]["title"] if m.startswith("unidentified-") else identity.display_name(m))
             for m in new_ids]
    model_name = ai.get("model_name") if ai.get("model_name") and len(new_ids) == 1 else " + ".join(names)
    words = [m for m in new_ids] + names
    whats_new = [i["text"] for i in ai.get("whats_new", [])]
    if not whats_new:
        order = {"changelog": 0, "announcement": 1}
        for t in sorted(texts, key=lambda t: order.get(t["kind"], 2)):
            whats_new += _first_sentences(t["text"], words, 2)
            if len(whats_new) >= 3:
                break
    unique = []
    for line in whats_new:  # the changelog and the blog post often say the same sentence
        if not any(ledger_similar(line, u) for u in unique):
            unique.append(line)
    whats_new = unique[:4]

    verification = CONFIRMED if all(r["verification"] == CONFIRMED for r in results) else REVIEW
    reasons = _dedupe_reasons([x for r in results for x in r["reasons"]])
    if verification == CONFIRMED and ai.get("is_new_model") == "no":
        verification = REVIEW
        reasons.append("the AI reading found the official text describes an existing model: "
                       + ai.get("classification_quote", {}).get("quote", "")[:160])
    if verification == CONFIRMED and unreadable and not any(t.get("read") or t["kind"] != "announcement"
                                                             for t in texts):
        verification = REVIEW
        reasons.append("the announcement could not be read, so nothing could be verified from it")

    summary = "; ".join(better[:2] + worse[:1]) or ("compared with " + prev["name"] if prev else "")
    flagship = is_flagship(new_ids, names, texts, company_cfg)
    return {
        "company": company, "bases": bases, "model_ids": new_ids, "model_name": model_name,
        "category": A, "verification": verification, "reasons": reasons,
        "announcement_date": announcement_date, "availability_date": availability_date,
        "availability_status": availability_status, "api_access": api_access,
        "texts": texts, "official": official, "specs": specs, "previous": prev, "previous_specs": prev_specs,
        "comparison": comparison, "improved": better, "regressed": worse, "unchanged": same,
        "whats_new": whats_new, "ai": ai, "ai_model": ai_model, "ai_dropped": ai_dropped,
        "comparison_summary": summary, "flagship": flagship,
        "priority": "P0" if verification == CONFIRMED and flagship else "P1",
        "explain": ai.get("explain") or fallback_explain(model_name, company_cfg, specs[main_id]["specs"], prev,
                                                         better + worse),
    }


FLAGSHIP_WORDS = re.compile(r"\b(flagship|most capable|most intelligent|most powerful|most advanced|frontier model|"
                            r"our best|state-of-the-art)\b", re.I)


def is_flagship(ids, names, texts, company_cfg):
    """A flagship launch (→ P0): the id matches the company's flagship patterns in releases.yaml, or the official
    text calls the model flagship / most capable / frontier in the same sentence as its name."""
    for pat in company_cfg.get("flagship_patterns", []):
        if any(re.search(pat, i, re.I) for i in ids):
            return True
    for t in texts:
        for sentence in re.split(r"(?<=[.!?])\s+", t.get("text") or ""):
            if FLAGSHIP_WORDS.search(sentence) and any(n.lower() in sentence.lower() for n in list(names) + list(ids)):
                return True
    return False


_PRICE = re.compile(r"\$\s?(\d+(?:\.\d+)?)\s*(?:per\s+(?:1M|one million|million)|/\s*(?:1M|M)?\s*)\s*input(?:\s+tokens?)?"
                    r"[^$]{0,60}?\$\s?(\d+(?:\.\d+)?)\s*(?:per\s+(?:1M|one million|million)|/\s*(?:1M|M)?\s*)\s*output",
                    re.I)


def prices_from_text(texts):
    """Input/output prices per 1M tokens stated in the official release text ('$1.50 per 1M input tokens and
    $7.50 per 1M output tokens'), used only when the catalog has no price for the new model."""
    for t in sorted(texts, key=lambda t: {"changelog": 0, "announcement": 1}.get(t["kind"], 2)):
        m = _PRICE.search(t.get("text") or "")
        if m:
            return {"input_price": float(m.group(1)), "output_price": float(m.group(2))}
    return {}


def fallback_explain(name, company_cfg, specs, prev, diffs):
    """Explanation from the official facts alone (no AI). Questions the facts can't answer are left out."""
    out = {"introduced": f"{name}, a new {company_cfg['product']} model from {company_cfg['label']}."}
    if specs.get("description"):
        out["does"] = specs["description"]
    if prev and diffs:
        out["different"] = f"Compared with {prev['name']}: " + "; ".join(diffs[:3]) + "."
    return out


def update_priority(category, verification):
    """Priority for the shorter alerts: upgrades and manual reviews P1, availability and early signals P2,
    documentation and feature notes P3."""
    if verification == REVIEW or category == B:
        return "P1"
    if category in (C, "UNCONFIRMED_EARLY_SIGNAL"):
        return "P2"
    return "P3"


def summarize_update(result, store, company_cfg):
    """Facts for the shorter alerts (upgrade, availability, early signal, docs/feature notes)."""
    obs = result["official"] or result["unofficial"]
    main = obs[0]
    ids = [m for o in obs for m in o.get("model_ids", []) if identity.base_key(m, company_cfg) == result["base"]]
    rec = (store.models(result["company"]).get(ids[0]) if ids else None) or {}
    known = [r for mid, r in store.models(result["company"]).items()
             if identity.base_key(mid, company_cfg) == result["base"]]
    return {
        "company": result["company"], "category": result["category"], "verification": result["verification"],
        "model_ids": list(dict.fromkeys(ids))[:4] or [result["base"]],
        "model_name": rec.get("name") or (known[0].get("name") if known else None) or identity.display_name(
            ids[0] if ids else result["base"]),
        "title": main.get("title", ""), "text": clean_text(main.get("text", ""), 500), "date": main.get("date"),
        "sources": [{"name": o["source"], "url": o.get("url")} for o in obs][:4], "reasons": result["reasons"],
        "priority": update_priority(result["category"], result["verification"]),
    }


def is_notifiable(category, verification):
    if verification == "REJECTED_AS_DUPLICATE":
        return False
    return category in (A, B, C) or verification == REVIEW
