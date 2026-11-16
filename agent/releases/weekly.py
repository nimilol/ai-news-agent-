"""The Weekly Model Intelligence Report (every Sunday, `python -m agent weekly-models`).

Built from the release monitor's persistent history (state/releases.json), not from the news briefs:

  1. New model releases       confirmed new models of the six labs announced (or first detected) in the last 7 days
  2. Model-to-model comparisons each new model against its predecessor, only with evidence
  3. Important model updates   upgrades, availability, pricing, retirements, API changes (kept apart from new models)
  4. Competitive landscape     what the evidence says across the six companies
  5. Benchmark intelligence    company-reported results and independent results (LMArena), never mixed
  6. Content opportunities     3 videos, 3 X posts, 1 comparison, 1 educational topic, ranked

Counting rules: a release is counted once, in the first weekly report after it was confirmed. One announced
earlier but only detected this week is included and marked "detected late". Releases already in an earlier report
are recorded in the history (meta.weekly_reported) and never counted again. Merged launches (two models in one
announcement) count as one release.
"""

import json
import re
from datetime import timedelta

from .. import llm
from .. import messages as msg
from ..common import ROOT, now_utc, parse_date
from . import benchmarks as bm
from . import identity
from .analyze import _parse_json, build_comparison, money, numbers_supported, spec_cells
from .classify import A, CONFIRMED, REVIEW, UNCONFIRMED

DAYS = 7
LAST_REPORT = ROOT / "state" / "last_model_weekly.json"
WEEKLY_ROWS = [("reasoning", "🧠 Reasoning"), ("coding", "💻 Coding"), ("multimodal", "🖼 Multimodal"),
               ("context", "📏 Context window"), ("speed", "⚡ Speed"), ("pricing", "💰 Pricing")]
CAPABILITIES = {
    "reasoning": re.compile(r"\b(reason\w*|think\w*|problem[- ]solving)\b", re.I),
    "coding": re.compile(r"\b(cod(e|ing)|software|programming|swe-?bench)\b", re.I),
    "agents and tool use": re.compile(r"\b(agent\w*|tool use|tools?|computer use|long-horizon)\b", re.I),
    "multimodal": re.compile(r"\b(vision|image|video|audio|multimodal|visual)\b", re.I),
    "long context": re.compile(r"\b(context window|1m[- ]token|million[- ]token|long context)\b", re.I),
    "speed and cost": re.compile(r"\b(fast\w*|speed|latency|cheap\w*|cost\w*|pric\w*|efficien\w*)\b", re.I),
}
UPDATE_GROUPS = [("upgrades", "⬆️ Major upgrades"), ("availability", "🟢 Availability changes"),
                 ("pricing", "💰 Pricing changes"), ("retirements", "🗑 Retirements and deprecations"),
                 ("api", "🔧 API and documentation changes")]

e = msg.e


# ----------------------------------------------------------------------------------------------- selection

def _in(value, start, end):
    """Is an ISO date/time inside [start, end]? A date without a time ("2026-11-09") counts by calendar day, and a
    month only ("2026-11") by its middle."""
    if not value:
        return False
    if "T" in value:
        d = parse_date(value)
        return bool(d) and start <= d <= end
    if len(value) == 7:
        value += "-15"
    return start.date().isoformat() <= value[:10] <= end.date().isoformat()


def _bases(store, rec):
    return {k for k, rid in store.index.items() if rid == rec["id"]} or {rec["id"]}


def select_releases(store, now, days=DAYS):
    """Confirmed new model releases for this week's report, each once. Returns [(record, late)]."""
    start = now - timedelta(days=days)
    reported = store.meta.get("weekly_reported", {})
    picked, seen = [], set()
    for rec in sorted(store.history.values(), key=lambda r: r.get("first_detected") or ""):
        if rec.get("classification") != A or rec.get("verification") != CONFIRMED or rec["id"] in reported:
            continue
        ann, det = rec.get("announcement_date"), rec.get("first_detected")
        ann_in, det_in = _in(ann, start, now), _in(det, start, now)
        if not ann_in and not det_in:
            continue
        bases = _bases(store, rec)
        if bases & seen:  # the same model through two records: count it once
            continue
        seen |= bases
        late = bool(det_in and ann and not ann_in)
        picked.append((rec, late))
    return picked


def _update_group(category, title):
    t = title.lower()
    if category == "MAJOR_MODEL_UPGRADE":
        return "upgrades"
    if category == "MODEL_AVAILABILITY":
        return "availability"
    if re.search(r"deprecat|retir|end of life|shut|restricted|no longer|→ deprecated", t):
        return "retirements"
    if re.search(r"pric|\$|cost|discount", t):
        return "pricing"
    if category == "DOCUMENTATION_OR_PRICING_CHANGE" or re.search(r"\bapi\b|endpoint|sdk|parameter|field", t):
        return "api"
    return None


def select_updates(store, now, days=DAYS):
    """Upgrades, availability, pricing, retirements and API changes of the week, grouped and without repeats."""
    start = now - timedelta(days=days)
    groups, seen = {k: [] for k, _ in UPDATE_GROUPS}, set()
    entries = []
    for rec in store.history.values():
        if rec.get("classification") in ("MAJOR_MODEL_UPGRADE", "MODEL_AVAILABILITY",
                                         "DOCUMENTATION_OR_PRICING_CHANGE") and _in(rec.get("first_detected"), start, now):
            entries.append({"company": rec["company"], "model": rec.get("model_name"), "category": rec["classification"],
                            "title": rec.get("comparison_summary") or "", "url": rec.get("source_url"),
                            "date": rec.get("announcement_date") or (rec.get("first_detected") or "")[:10]})
    for ch in store.changes:
        if _in(ch.get("at"), start, now):
            entries.append({"company": ch["company"], "model": ch.get("model"), "category": ch.get("category"),
                            "title": ch.get("title", ""), "url": None, "date": (ch.get("at") or "")[:10]})
    for en in entries:
        group = _update_group(en["category"], f"{en['title']} {en['model']}")
        key = (en["company"], (en["model"] or "").lower(), re.sub(r"\W+", " ", en["title"].lower())[:80])
        if not group or key in seen:
            continue
        seen.add(key)
        groups[group].append(en)
    return {k: v[:8] for k, v in groups.items() if v}


def select_watchlist(store, now, days=DAYS):
    start = now - timedelta(days=days)
    return [r for r in store.history.values() if r.get("verification") in (UNCONFIRMED, REVIEW)
            and _in(r.get("first_detected"), start, now)]


# ----------------------------------------------------------------------------------------------- comparisons & benchmarks

def _prev_base(rec, company_cfg):
    prev = rec.get("previous_model") or {}
    name = prev.get("id") or prev.get("name")
    return identity.base_key(name, company_cfg) if name else None


def _main_base(store, rec, company_cfg):
    ids = rec.get("model_ids") or []
    return identity.base_key(ids[0], company_cfg) if ids else sorted(_bases(store, rec))[0].split(":", 1)[-1]


def comparison_for(store, rec, companies, bench):
    """Rows for one release: official specs and company statements (from the history), company-reported
    benchmarks, and independent results from the same leaderboard category for both models."""
    cfg = companies[rec["company"]]
    d = rec.get("details") or {}
    rows = d.get("comparison")
    if rows is None and rec.get("previous_model"):
        main = (rec.get("model_ids") or [None])[0]
        new_specs = ((d.get("specs") or {}).get(main) or {}).get("specs", {})
        rows = build_comparison(d.get("previous_specs") or {}, new_specs, None, None, {})
    rows = rows or {}
    independent = []
    base, prev_base = _main_base(store, rec, cfg), _prev_base(rec, cfg)
    for source, boards in bm.lookup(bench, cfg, base).items():
        prev_rows = bm.lookup(bench, cfg, prev_base).get(source, {}) if prev_base else {}
        for label, row in boards.items():
            prev_row = prev_rows.get(label)
            independent.append({"source": source, "board": label, "new": row, "prev": prev_row,
                                "verdict": bm.compare(row, prev_row)})
    return {"rows": rows, "company_benchmarks": d.get("benchmarks") or [], "independent": independent}


# ----------------------------------------------------------------------------------------------- landscape

def landscape_facts(releases, updates, companies, comparisons, bench, store):
    """Evidence for section 4, all computed from the history and the benchmark data (no AI)."""
    activity = {}
    for key, c in companies.items():
        mine = [r for r, _ in releases if r["company"] == key]
        upd = sum(1 for g in updates.values() for x in g if x["company"] == key)
        activity[key] = {"label": c["label"], "new": [r["model_name"] for r in mine], "updates": upd,
                         "flagship": [r["model_name"] for r in mine if (r.get("details") or {}).get("flagship")]}
    tally = {}
    for r, _ in releases:
        d = r.get("details") or {}
        text = " ".join(d.get("whats_new") or []) + " " + " ".join((d.get("explain") or {}).values())
        for cap, pat in CAPABILITIES.items():
            if pat.search(text):
                tally.setdefault(cap, []).append(r["model_name"])
    contexts, prices = [], []
    for r, _ in releases:
        specs = ((r.get("details") or {}).get("specs") or {})
        main = specs.get((r.get("model_ids") or [None])[0], {}).get("specs", {})
        if main.get("context_tokens"):
            contexts.append((r["model_name"], main["context_tokens"]))
        if main.get("input_price") is not None:
            prices.append((r["model_name"], main["input_price"], main.get("output_price")))
    cheaper = [r["model_name"] for r, _ in releases if any("price drops" in x.lower()
                                                          for x in (r.get("details") or {}).get("improved", []))]
    pricier = [r["model_name"] for r, _ in releases if any("price rises" in x.lower()
                                                          for x in (r.get("details") or {}).get("regressed", []))]
    bigger_ctx = [r["model_name"] for r, _ in releases if any("context window grows" in x.lower()
                                                             for x in (r.get("details") or {}).get("improved", []))]
    no_company_bench = [r["model_name"] for r, _ in releases if not (r.get("details") or {}).get("benchmarks")]
    no_independent = [r["model_name"] for (r, _), c in zip(releases, comparisons) if not c["independent"]]
    independent_drops = [f"{r['model_name']} ({bm.label(bench, x['source'], x['board'])})"
                         for (r, _), c in zip(releases, comparisons) for x in c["independent"]
                         if x["prev"] and bm.significant(x["prev"], x["new"])]
    independent_gains = [f"{r['model_name']} ({bm.label(bench, x['source'], x['board'])})"
                         for (r, _), c in zip(releases, comparisons) for x in c["independent"]
                         if x["prev"] and bm.significant(x["new"], x["prev"])]
    limited = [r["model_name"] for r, _ in releases
               if re.search(r"preview|limited|restricted", r.get("availability_status") or "", re.I)]
    leaders = bm.leaders(bench, companies)
    return {"activity": activity, "capabilities": tally, "contexts": contexts, "prices": prices, "cheaper": cheaper,
            "pricier": pricier, "bigger_context": bigger_ctx, "no_company_benchmarks": no_company_bench,
            "no_independent": no_independent, "independent_gains": independent_gains,
            "independent_drops": independent_drops, "limited": limited,
            "leaders": [{"company": x["company"], "model": x["name"], "rating": round(x["rating"]),
                         "low": round(x["rating_lower"]), "high": round(x["rating_upper"])} for x in leaders]}


def landscape_answers(f, companies):
    """Plain, evidence-only answers to the four landscape questions (used when no AI is available)."""
    active = [a for a in f["activity"].values() if a["new"] or a["updates"]]
    quiet = [a["label"] for a in f["activity"].values() if not a["new"] and not a["updates"]]
    flag = [f"{a['label']} ({', '.join(a['flagship'])})" for a in f["activity"].values() if a["flagship"]]
    improvements = ("Flagship releases: " + "; ".join(flag) + ". ") if flag else ""
    improvements += ("New models from " + ", ".join(f"{a['label']} ({len(a['new'])})" for a in active if a["new"])
                     + ". ") if any(a["new"] for a in active) else "No new models this week. "
    if f["independent_gains"]:
        improvements += "Measurably better than their predecessor on LMArena: " + ", ".join(f["independent_gains"]) + ". "
    if quiet:
        improvements += "Quiet: " + ", ".join(quiet) + "."
    caps = sorted(f["capabilities"].items(), key=lambda kv: -len(kv[1]))
    advancing = "; ".join(f"{cap}: {len(models)} of the new models' official descriptions"
                          for cap, models in caps[:4]) or "Not enough new releases to see a trend."
    if f["bigger_context"]:
        advancing += f". Larger context than the predecessor: {', '.join(f['bigger_context'])}"
    conv = []
    big = [n for n, c in f["contexts"] if c >= 1_000_000]
    if len(big) >= 2:
        conv.append(f"{len(big)} new models offer a context window of 1M tokens or more ({', '.join(big)})")
    if len(f["prices"]) >= 2:
        lo, hi = min(f["prices"], key=lambda p: p[1]), max(f["prices"], key=lambda p: p[1])
        conv.append(f"input prices range from {money(lo[1])} ({lo[0]}) to {money(hi[1])} ({hi[0]}) per 1M tokens")
    if f["cheaper"]:
        conv.append("cheaper than their predecessor: " + ", ".join(f["cheaper"]))
    if f["pricier"]:
        conv.append("more expensive than their predecessor: " + ", ".join(f["pricier"]))
    if len(f["leaders"]) >= 2:
        top, second = f["leaders"][0], f["leaders"][1]
        overlap = top["low"] <= second["high"]
        conv.append(f"on LMArena (general) the best models of {companies[top['company']]['label']} and "
                    f"{companies[second['company']]['label']} are "
                    + ("within each other's confidence intervals" if overlap else f"{top['rating'] - second['rating']} points apart"))
    converging = "; ".join(conv) + "." if conv else "Not enough comparable data this week."
    lim = []
    if f["no_company_benchmarks"]:
        lim.append("no benchmark numbers in the official sources for " + ", ".join(f["no_company_benchmarks"]))
    if f["no_independent"]:
        lim.append("no independent results yet for " + ", ".join(f["no_independent"]))
    if f["independent_drops"]:
        lim.append("measurably below their predecessor on LMArena: " + ", ".join(f["independent_drops"]))
    if f["limited"]:
        lim.append("preview or limited availability: " + ", ".join(f["limited"]))
    if f["pricier"]:
        lim.append("price increases: " + ", ".join(f["pricier"]))
    limitations = ("; ".join(lim) + ".") if lim else "No specific limitations recorded in the evidence this week."
    cap = lambda t: t[:1].upper() + t[1:]  # noqa: E731
    return {"improvements": cap(improvements.strip()), "advancing": cap(advancing), "converging": cap(converging),
            "limitations": cap(limitations)}


# ----------------------------------------------------------------------------------------------- content

REPORT_PROMPT = """You help an AI creator and educator plan content from this week's evidence about new AI models.
You get FACTS as JSON: confirmed releases (numbered), their comparisons with predecessors, independent benchmark
results, updates, and evidence-based landscape notes. Use only these facts; never invent numbers or claims.

Answer with JSON only:
{"landscape": {"improvements": "", "advancing": "", "converging": "", "limitations": ""},
 "videos": [{"title": "", "angle": "", "why": "", "research": "", "releases": [1], "relevance": 1, "education": 1}],
 "posts": [{"text": "an X post under 260 characters, factual, no hashtags spam", "releases": [1], "relevance": 1, "education": 1}],
 "comparison": {"title": "", "angle": "", "why": "", "research": "", "releases": [1], "relevance": 1, "education": 1},
 "education": {"title": "", "angle": "", "why": "", "research": "", "releases": [1], "relevance": 1, "education": 1}}

Rules: exactly 3 videos and 3 posts. "landscape" answers, in 1-3 sentences each and only from the facts: which
companies introduced significant improvements, which capabilities are advancing, where competitors converge or
differ, and what important limitations remain. Never compare company-reported scores with LMArena ratings, and
never compare scores from different benchmarks. relevance and education are 1-5 (how timely and important; how
much a viewer learns). "research" says what must be checked or tested before publishing."""


def evidence_score(rel_numbers, releases, comparisons):
    """How strong the evidence behind an idea is (1-5): official confirmation, specs, benchmarks, independent data."""
    if not rel_numbers:
        return 2
    scores = []
    for n in rel_numbers:
        if not 1 <= n <= len(releases):
            continue
        rec, comp = releases[n - 1][0], comparisons[n - 1]
        s = 3  # officially confirmed
        if rec.get("previous_model") and comp["rows"]:
            s += 1
        if comp["independent"] or comp["company_benchmarks"]:
            s += 1
        scores.append(s)
    return min(scores) if scores else 2


def _score(item, releases, comparisons):
    rel = [int(n) for n in item.get("releases") or [] if str(n).isdigit()]
    clamp = lambda v: max(1, min(5, int(v))) if str(v).isdigit() else 3  # noqa: E731
    item["scores"] = {"relevance": clamp(item.get("relevance", 3)), "evidence": evidence_score(rel, releases, comparisons),
                      "education": clamp(item.get("education", 3))}
    item["total"] = sum(item["scores"].values())
    return item


def fallback_content(releases, comparisons, companies):
    """Ideas without AI, from the strongest evidence of the week."""
    if not releases:
        return {"videos": [], "posts": [], "comparison": None, "education": None}
    order = sorted(range(len(releases)), key=lambda i: (
        {"P0": 0, "P1": 1}.get((releases[i][0].get("details") or {}).get("priority"), 2), -len(comparisons[i]["independent"])))
    top = [releases[i][0] for i in order]
    num = {r["id"]: i + 1 for i, (r, _) in enumerate(releases)}
    lead = top[0]
    lab = lambda r: companies[r["company"]]["label"]  # noqa: E731
    prev = lambda r: (r.get("previous_model") or {}).get("name") or "its predecessor"  # noqa: E731
    videos = [{"title": f"{lab(r)}'s {r['model_name']}: what's new and who it's for",
               "angle": "What changed, what it costs and where you can use it.",
               "why": "Officially confirmed this week.", "research": "Read the announcement; try it on your own tasks.",
               "releases": [num[r["id"]]], "relevance": 5 if i == 0 else 4, "education": 3} for i, r in enumerate(top[:3])]
    posts = []
    for r in top[:3]:
        specs = ((r.get("details") or {}).get("specs") or {}).get((r.get("model_ids") or [None])[0], {}).get("specs", {})
        cells = spec_cells(specs)
        bits = [x for x in ((f"{cells['context']} context" if cells.get("context") else None), cells.get("pricing")) if x]
        text = f"{lab(r)} released {r['model_name']}" + (f": {'; '.join(bits)}" if bits else "") + \
               (f". Official: {r['source_url']}" if r.get("source_url") else ".")
        posts.append({"text": msg.clip(text, 260), "releases": [num[r["id"]]], "relevance": 4, "education": 2})
    comp = next((r for r in top if r.get("previous_model")), lead)
    comparison = {"title": f"{comp['model_name']} vs {prev(comp)}: what actually changed",
                  "angle": "Official specs side by side, plus independent results from the same leaderboard.",
                  "why": "Separates real improvements from launch claims.",
                  "research": "Run the same prompts on both; check LMArena confidence intervals before calling a winner.",
                  "releases": [num[comp["id"]]], "relevance": 4, "education": 4}
    education = {"title": f"How to read model benchmarks: company numbers vs LMArena ({lead['model_name']} as example)",
                 "angle": "Why self-reported scores and crowdsourced ratings can't be compared directly.",
                 "why": "Every launch comes with numbers; viewers need to know what they mean.",
                 "research": "Read the benchmark descriptions in the announcement and LMArena's method.",
                 "releases": [num[lead["id"]]], "relevance": 3, "education": 5}
    return {"videos": videos, "posts": posts, "comparison": comparison, "education": education}


def plan_content(config, releases, comparisons, updates, facts, companies):
    """One AI call for the landscape wording and the content ideas; checked against the facts, ranked; falls back
    to evidence-only wording and ideas."""
    answers = landscape_answers(facts, companies)
    content = fallback_content(releases, comparisons, companies)
    model_used = None
    payload = {"releases": [{"n": i + 1, "company": companies[r["company"]]["label"], "model": r["model_name"],
                             "announced": r.get("announcement_date"), "whats_new": (r.get("details") or {}).get("whats_new"),
                             "previous": (r.get("previous_model") or {}).get("name"),
                             "comparison": {k: {"previous": v.get("previous"), "new": v.get("new")}
                                            for k, v in comparisons[i]["rows"].items()},
                             "company_benchmarks": comparisons[i]["company_benchmarks"],
                             "independent": [{"board": x["board"], "new": round(x["new"]["rating"]),
                                              "previous": round(x["prev"]["rating"]) if x["prev"] else None,
                                              "verdict": x["verdict"]} for x in comparisons[i]["independent"]]}
                            for i, (r, _) in enumerate(releases)],
               "updates": {k: [f"{x['model']}: {x['title']}" for x in v] for k, v in updates.items()},
               "landscape_evidence": answers}
    corpus = json.dumps(payload, ensure_ascii=False)
    if releases:
        try:
            text, model_used = llm.complete(config, REPORT_PROMPT, lambda **_: "FACTS:\n" + corpus)
            data = _parse_json(text)
            check = lambda v: numbers_supported(v, corpus)  # noqa: E731
            for k, v in (data.get("landscape") or {}).items():
                if k in answers and isinstance(v, str) and v.strip() and check(v):
                    answers[k] = msg.clip(v, 600)
            vids = [x for x in data.get("videos") or [] if isinstance(x, dict) and x.get("title") and
                    all(check(str(x.get(f, ""))) for f in ("title", "angle", "why"))]
            posts = [x for x in data.get("posts") or [] if isinstance(x, dict) and x.get("text") and check(x["text"])]
            if len(vids) >= 3:
                content["videos"] = vids[:3]
            if len(posts) >= 3:
                content["posts"] = [{**p, "text": msg.clip(p["text"], 270)} for p in posts[:3]]
            for key in ("comparison", "education"):
                x = data.get(key)
                if isinstance(x, dict) and x.get("title") and all(check(str(x.get(f, ""))) for f in ("title", "angle", "why")):
                    content[key] = x
        except Exception as ex:  # noqa: BLE001 - the report goes out with evidence-only wording
            print(f"  AI planning skipped: {str(ex)[:160]}")
            model_used = None
    for key in ("videos", "posts"):
        content[key] = sorted((_score(x, releases, comparisons) for x in content[key]), key=lambda x: -x["total"])
    for key in ("comparison", "education"):
        if content.get(key):
            _score(content[key], releases, comparisons)
    return answers, content, model_used


# ----------------------------------------------------------------------------------------------- rendering

def _date(value):
    return msg.human_date(value) if value else "not stated"


def _links(rec):
    out = []
    ann = next((s for s in rec.get("official_sources", []) if s.get("kind") == "announcement" and s.get("url")), None)
    doc = next((s for s in rec.get("official_sources", []) if s.get("kind") == "changelog" and s.get("url")), None)
    if ann:
        out.append(msg.link("Official announcement", ann["url"]))
    elif rec.get("source_url"):
        out.append(msg.link("Official source", rec["source_url"]))
    shown = ann["url"] if ann else rec.get("source_url")
    if doc and doc["url"] != shown:
        out.append(msg.link("Release notes", doc["url"]))
    return " · ".join(out)


def _release_block(n, rec, late, companies):
    d = rec.get("details") or {}
    c = companies[rec["company"]]
    p = d.get("priority") or "P1"
    lines = [f"<b>{n}. {e(rec['model_name'])}</b> · {e(c['label'])} ({e(c['product'])})",
             f"<i>{msg.priority_label(p)}{' · flagship' if d.get('flagship') else ''} · "
             f"{', '.join('<code>' + e(m) + '</code>' for m in rec.get('model_ids', [])[:3])}</i>",
             f"📣 Announced: {e(_date(rec.get('announcement_date')))} · 🟢 Available: {e(_date(rec.get('availability_date')))}"]
    if late:
        lines.append(f"⏱ <i>Detected late: first seen {e(msg.human_date((rec.get('first_detected') or '')[:10]))}</i>")
    caps = (d.get("whats_new") or [])[:3]
    if caps:
        lines.append("<b>Main capabilities:</b>\n" + "\n".join(f"• {e(msg.clip(x, 220))}" for x in caps))
    lines.append(f"<b>API / product:</b> {e(msg.clip(d.get('api_access') or 'not stated', 200)).replace('`', '')}"
                 + (f" · {e(rec['availability_status'])}" if rec.get("availability_status") else ""))
    links = _links(rec)
    if links:
        lines.append("🔗 " + links)
    return "\n".join(lines)


def _comparison_block(n, rec, comp, bench):
    prev = (rec.get("previous_model") or {}).get("name")
    if not prev:
        return f"<b>{n}. {e(rec['model_name'])}</b>\n<i>No previous model could be established from the official sources.</i>"
    lines = [f"<b>{n}. {e(rec['model_name'])} vs {e(prev)}</b>",
             f"<i>Predecessor: {e((rec.get('previous_model') or {}).get('method') or '')}</i>"]
    missing = []
    for key, label in WEEKLY_ROWS:
        cell = comp["rows"].get(key)
        if not cell or not (cell.get("previous") or cell.get("new")):
            missing.append(label.split(" ", 1)[1])
            continue
        lines.append(f"{label}: {e(cell.get('previous') or 'not stated')} → {e(cell.get('new') or 'not stated')}")
    same_bench = [b for b in comp["company_benchmarks"] if b.get("previous")]
    for b in same_bench:
        lines.append(f"📈 {e(b['name'])} (company-reported): {e(str(b['previous']))} → {e(str(b['new']))}")
    for x in comp["independent"]:
        if x["prev"]:
            lines.append(f"📊 LMArena {e(bm.label(bench, x['source'], x['board']))}: "
                         f"{x['prev']['rating']:.0f} → {x['new']['rating']:.0f} ({e(x['verdict'])})")
    if not same_bench and not any(x["prev"] for x in comp["independent"]):
        missing.append("Benchmark results for both models on the same evaluation")
    if missing:
        lines.append(f"<i>Not established: {e(', '.join(missing))}</i>")
    return "\n".join(lines)


def _updates_block(updates, watchlist, companies):
    if not updates and not watchlist:
        return "<i>No upgrades, pricing changes, retirements or API changes recorded this week.</i>"
    parts = []
    for key, label in UPDATE_GROUPS:
        items = updates.get(key) or []
        if items:
            rows = [f"• <b>{e(companies.get(x['company'], {}).get('label', x['company']))}</b> · "
                    f"{e(msg.clip(x['model'] or '', 60))}: {e(msg.clip(x['title'], 160))}"
                    + (f" ({msg.link('source', x['url'])})" if x.get("url") else "") for x in items]
            parts.append(f"<b>{label}</b>\n" + "\n".join(rows))
    if watchlist:
        rows = [f"• {e(r['model_name'])} ({e(companies.get(r['company'], {}).get('label', r['company']))}): "
                f"{'unconfirmed early signal' if r['verification'] == UNCONFIRMED else 'requires manual review'}"
                for r in watchlist[:8]]
        parts.append("<b>👀 Not confirmed yet (not counted as releases)</b>\n" + "\n".join(rows))
    return "\n\n".join(parts)


def _landscape_block(answers, facts, companies):
    act = []
    for a in facts["activity"].values():
        bits = []
        if a["new"]:
            bits.append(f"{len(a['new'])} new ({', '.join(a['new'])})")
        if a["updates"]:
            bits.append(f"{a['updates']} update{'s' if a['updates'] != 1 else ''}")
        act.append(f"• <b>{e(a['label'])}</b>: {e('; '.join(bits) or 'no new models or recorded updates')}")
    qa = [("Which companies introduced significant improvements?", "improvements"),
          ("Which capabilities are advancing?", "advancing"),
          ("Where are competitors converging or differentiating?", "converging"),
          ("What important limitations remain?", "limitations")]
    lines = ["<b>This week by company</b>", *act, ""]
    for q, key in qa:
        lines.append(f"<b>{q}</b>\n{e(answers[key])}")
    if facts["leaders"]:
        lines.append("<b>Where each company stands on LMArena (general, text)</b>\n" + "\n".join(
            f"• {e(companies[x['company']]['label'])}: {e(x['model'])} {x['rating']} (95% CI {x['low']}-{x['high']})"
            for x in facts["leaders"]))
    return "\n".join(lines)


def _benchmark_block(releases, comparisons, cfg, bench):
    lines = ["<b>Company-reported results</b> <i>(from the official announcements; methodology as described by each "
             "company; not independently verified)</i>"]
    any_company = False
    for (rec, _), comp in zip(releases, comparisons):
        for b in comp["company_benchmarks"]:
            any_company = True
            prev = f" (previous: {e(str(b['previous']))})" if b.get("previous") else ""
            lines.append(f"• {e(rec['model_name'])} · {e(str(b.get('name')))}: {e(str(b.get('new')))}{prev}"
                         + (f" · {msg.link('source', rec['source_url'])}" if rec.get("source_url") else ""))
    if not any_company:
        lines.append("• None found in the official sources this week.")
    for src in cfg.get("independent_benchmarks", []):
        if src["name"] not in bench:
            lines.append(f"<b>{e(src['name'])}</b>: <i>not available this week.</i>")
            continue
        boards = bench[src["name"]]["boards"]
        dates = {r.get("leaderboard_publish_date") for rows in boards.values() for r in rows[:1]}
        lines.append(f"\n<b>Independent: {e(src['name'])}</b> <i>({e(src['methodology'])} "
                     f"Data of {e(', '.join(sorted(d for d in dates if d)) or 'unknown date')}.)</i> "
                     + msg.link("dataset", src.get("url")))
        found = False
        for (rec, _), comp in zip(releases, comparisons):
            for x in comp["independent"]:
                if x["source"] != src["name"]:
                    continue
                found = True
                line = f"• {e(rec['model_name'])} · {e(bm.label(bench, x['source'], x['board']))}: {e(bm.describe(x['new']))}"
                if x["prev"]:
                    line += f"\n  vs {e(bm.describe(x['prev']))} → {e(x['verdict'])}"
                lines.append(line)
        if not found:
            lines.append("• No ratings yet for this week's new models (new models need votes before they appear)."
                         if releases else "• No new models to look up this week.")
    lines.append("<i>Scores from different evaluations are never compared with each other: a company's benchmark "
                 "number and an LMArena rating measure different things.</i>")
    return "\n".join(lines)


def _content_block(content):
    def scores(x):
        s = x["scores"]
        return (f"<i>Score {x['total']}/15 · relevance {s['relevance']} · evidence {s['evidence']} · "
                f"education {s['education']}</i>")
    lines = []
    if content["videos"]:
        lines.append("<b>🎬 Video topics (ranked)</b>")
        for i, v in enumerate(content["videos"], 1):
            lines.append(f"{i}. <b>{e(v['title'])}</b>\n{scores(v)}"
                         + (f"\nAngle: {e(v['angle'])}" if v.get("angle") else "")
                         + (f"\nWhy: {e(v['why'])}" if v.get("why") else "")
                         + (f"\nResearch first: {e(v['research'])}" if v.get("research") else ""))
    if content["posts"]:
        lines.append("\n<b>𝕏 Posts (ranked)</b>")
        for i, p in enumerate(content["posts"], 1):
            lines.append(f"{i}. {e(p['text'])}\n{scores(p)}")
    for key, label in (("comparison", "⚖️ Model comparison"), ("education", "📚 Deeper educational topic")):
        x = content.get(key)
        if x:
            lines.append(f"\n<b>{label}</b>\n<b>{e(x['title'])}</b>\n{scores(x)}"
                         + (f"\nAngle: {e(x['angle'])}" if x.get("angle") else "")
                         + (f"\nResearch first: {e(x['research'])}" if x.get("research") else ""))
    lines.append("\n<i>Ideas only: nothing is created or published. Evidence = official confirmation, specs and "
                 "benchmark data behind the topic.</i>")
    return "\n".join(lines)


def render(report, companies, cfg):
    """The report as Telegram messages: six numbered sections with clear headings."""
    releases, comparisons = report["releases"], report["comparisons"]
    start, end = report["start"], report["end"]
    labels = {r["company"] for r, _ in releases}
    head = (f"<b>🧠 Weekly Model Intelligence Report</b>\n<i>{start:%-d %b} – {end:%-d %b %Y} · Anthropic, OpenAI, "
            f"Moonshot AI, Alibaba, xAI, Google</i>\n"
            f"{len(releases)} new model release{'s' if len(releases) != 1 else ''} from {len(labels)} "
            f"compan{'ies' if len(labels) != 1 else 'y'} · "
            f"{sum(len(v) for v in report['updates'].values())} updates")
    blocks = [(head, None)]

    def section(num, emoji, title, body):
        blocks.append((f"<b>{num}. {emoji} {title}</b>\n{body}", None))

    if releases:
        section(1, "🚨", "NEW MODEL RELEASES", "\n\n".join(_release_block(i, r, late, companies)
                                                         for i, (r, late) in enumerate(releases, 1)))
        section(2, "↔️", "MODEL-TO-MODEL COMPARISONS", "\n\n".join(
            _comparison_block(i, r, c, report["bench"]) for i, ((r, _), c) in enumerate(zip(releases, comparisons), 1)))
    else:
        section(1, "🚨", "NEW MODEL RELEASES", "<i>No new model was officially confirmed by the six labs this week.</i>")
        section(2, "↔️", "MODEL-TO-MODEL COMPARISONS", "<i>No new releases to compare.</i>")
    section(3, "🔄", "IMPORTANT MODEL UPDATES", _updates_block(report["updates"], report["watchlist"], companies))
    section(4, "🧭", "COMPETITIVE LANDSCAPE", _landscape_block(report["answers"], report["facts"], companies))
    section(5, "📈", "BENCHMARK INTELLIGENCE", _benchmark_block(releases, comparisons, cfg, report["bench"]))
    if report["content"]["videos"] or report["content"]["posts"]:
        section(6, "💡", "CONTENT OPPORTUNITIES", _content_block(report["content"]))
    footer = ("Built from the release monitor's history (state/releases.json), each release counted once"
              + (f" · wording by {report['model']}" if report.get("model") else " · evidence-only wording (no AI)"))
    blocks.append((f"<i>{e(footer)}</i>", None))
    return msg.paginate(blocks)


# ----------------------------------------------------------------------------------------------- build & record

def build(store, cfg, config, now=None, bench=None):
    now = now or now_utc()
    companies = cfg["companies"]
    releases = select_releases(store, now)
    updates = select_updates(store, now)
    watchlist = select_watchlist(store, now)
    if bench is None:
        print("Reading independent benchmarks…")
        bench = bm.fetch_all(cfg)
    comparisons = [comparison_for(store, r, companies, bench) for r, _ in releases]
    facts = landscape_facts(releases, updates, companies, comparisons, bench, store)
    answers, content, model = plan_content(config, releases, comparisons, updates, facts, companies)
    return {"start": now - timedelta(days=DAYS), "end": now, "releases": releases, "comparisons": comparisons,
            "updates": updates, "watchlist": watchlist, "facts": facts, "answers": answers, "content": content,
            "model": model, "bench": bench}


def mark_reported(store, report):
    """Remember which releases this report covered, so the next one never counts them again."""
    reported = store.meta.setdefault("weekly_reported", {})
    day = report["end"].date().isoformat()
    for rec, _ in report["releases"]:
        reported[rec["id"]] = day


def archive(report, messages):
    """A Markdown copy in releases/weekly/ (next to the release alert archive)."""
    from .render import plain

    path = ROOT / "releases" / "weekly" / f"{report['end']:%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Weekly Model Intelligence Report · {report['start']:%-d %b} – {report['end']:%-d %b %Y}\n\n"
                    + plain(messages) + "\n", encoding="utf-8")
    return path


def save_last(messages):
    LAST_REPORT.parent.mkdir(exist_ok=True)
    LAST_REPORT.write_text(json.dumps(messages, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def top_priority(report):
    return msg.best_priority([(r.get("details") or {}).get("priority") or "P1" for r, _ in report["releases"]] or ["P2"])
