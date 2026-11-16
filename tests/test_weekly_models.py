"""Offline tests for the Weekly Model Intelligence Report (agent/releases/weekly.py) and the independent benchmark
reader (agent/releases/benchmarks.py). The release history and LMArena are faked; nothing touches the network."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import agent.__main__ as main
from agent import messages as msg
from agent.releases import benchmarks as bm, config as rconfig, weekly as w
from agent.releases.store import Store

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)   # a Sunday
CFG = rconfig.load()
COMPANIES = CFG["companies"]
NO_AI = {"llm": {"order": [], "providers": {}}}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.state.ROOT", tmp_path)
    monkeypatch.setattr("agent.releases.weekly.ROOT", tmp_path)
    monkeypatch.setattr("agent.releases.weekly.LAST_REPORT", tmp_path / "state" / "last_model_weekly.json")
    monkeypatch.setattr("agent.botpush.push", lambda *a, **k: True)
    return tmp_path


def lm_row(name, rating, low, high, rank=1, votes=1000, category="overall", org="google"):
    return {"model_name": name, "organization": org, "rating": rating, "rating_lower": low, "rating_upper": high,
            "rank": rank, "vote_count": votes, "category": category, "leaderboard_publish_date": "2026-11-20"}


BENCH = {"LMArena": {
    "source": CFG["independent_benchmarks"][0],
    "labels": {"text/overall": "General (text)", "text/coding": "Coding"},
    "boards": {
        "text/overall": [lm_row("Gemini 5 Pro (High)", 1560, 1550, 1570, 1), lm_row("Gemini 5 Pro (Low)", 1500, 1490, 1510, 9),
                         lm_row("Gemini 4 Carbon (High)", 1538, 1529, 1548, 2),
                         lm_row("Kimi K4 (Max)", 1470, 1460, 1480, 30, org="moonshot"),
                         lm_row("Kimi K3 (Max)", 1475, 1470, 1479, 28, org="moonshot"),
                         lm_row("Claude Opus 5.5 (High)", 1514, 1508, 1520, 3, org="anthropic")],
        "text/coding": [lm_row("Gemini 5 Pro (High)", 1580, 1560, 1600, 1, category="coding"),
                        lm_row("Gemini 4 Carbon (High)", 1575, 1565, 1585, 2, category="coding")],
    }}}


def release(store, company, base, name, announced, detected, verification="OFFICIAL_CONFIRMED",
            classification="NEW_MODEL_RELEASE", prev=None, benchmarks=(), specs=None, prev_specs=None, priority="P1",
            flagship=False, whats_new=("Better at coding and reasoning.",), comparison=None):
    rec = store.new_release(company, [base], model_name=name, model_ids=[base], classification=classification,
                            verification=verification, announcement_date=announced,
                            availability_date=announced, availability_status="Generally available",
                            source_url=f"https://example.com/{base}",
                            official_sources=[{"source": "blog", "url": f"https://example.com/{base}",
                                               "kind": "announcement"}],
                            previous_model={"name": prev[1], "id": prev[0], "method": "named by the company"} if prev else None)
    rec["first_detected"] = detected
    rec["details"] = {"whats_new": list(whats_new), "benchmarks": list(benchmarks), "priority": priority,
                      "flagship": flagship, "api_access": f"Yes: `{base}`",
                      "specs": {base: {"specs": specs or {}}}, "previous_specs": prev_specs or {},
                      "improved": [], "regressed": [], "explain": {}}
    if comparison is not None:
        rec["details"]["comparison"] = comparison
    return rec


@pytest.fixture
def week(isolated):
    s = Store()
    release(s, "google", "gemini-5-pro", "Gemini 5 Pro", "2026-11-18", "2026-11-18T10:00:00+00:00",
            prev=("gemini-4-carbon", "Gemini 4 Carbon"), priority="P0", flagship=True,
            specs={"context_tokens": 2_000_000, "input_price": 2.0, "output_price": 12.0},
            prev_specs={"context_tokens": 1_000_000, "input_price": 2.0, "output_price": 10.0},
            benchmarks=[{"name": "SWE-bench Verified", "new": "81.2%", "previous": "74.0%"}])
    release(s, "moonshot", "kimi-k-4", "Kimi K4", "2026-11-02", "2026-11-20T09:00:00+00:00",      # announced earlier,
            prev=("kimi-k3", "Kimi K3"))                                                         # detected late
    release(s, "openai", "gpt-7", "GPT-7", "2026-11-10", "2026-11-10T09:00:00+00:00")           # before the window
    release(s, "xai", "grok-6", "Grok 6", "2026-11-19", "2026-11-19T09:00:00+00:00", verification="UNCONFIRMED",
            classification="UNCONFIRMED_EARLY_SIGNAL")
    release(s, "anthropic", "claude-opus-6", "Claude Opus 6", "2026-11-17", "2026-11-17T09:00:00+00:00")
    s.meta["weekly_reported"] = {"anthropic:claude-opus-6": "2026-11-17"}                       # already reported
    s.history["google:gemini-5-pro@MODEL_AVAILABILITY:2026-11"] = {
        "id": "google:gemini-5-pro@MODEL_AVAILABILITY:2026-11", "company": "google", "model_name": "Gemini 5 Pro",
        "classification": "MODEL_AVAILABILITY", "verification": "OFFICIAL_UPDATE",
        "first_detected": "2026-11-20T09:00:00+00:00", "comparison_summary": "Now available in the EU",
        "source_url": "https://ai.google.dev/x", "announcement_date": "2026-11-20"}
    s.changes[:0] = [
        {"company": "openai", "model": "gpt-5.2", "category": "DOCUMENTATION_OR_PRICING_CHANGE",
         "title": "gpt-5.2: active → deprecated (OpenAI model catalog)", "at": "2026-11-19T09:00:00+00:00"},
        {"company": "anthropic", "model": "Claude Haiku 5.5", "category": "DOCUMENTATION_OR_PRICING_CHANGE",
         "title": "Claude Haiku 5.5: details changed, input price $0.10 → $0.08", "at": "2026-11-21T09:00:00+00:00"},
        {"company": "anthropic", "model": "Claude Haiku 5.5", "category": "DOCUMENTATION_OR_PRICING_CHANGE",
         "title": "Claude Haiku 5.5: details changed, input price $0.10 → $0.08", "at": "2026-11-21T10:00:00+00:00"},
        {"company": "xai", "model": "grok-4.7", "category": "PRODUCT_OR_FEATURE_ANNOUNCEMENT",
         "title": "New safety_identifier API field", "at": "2026-11-20T09:00:00+00:00"},
        {"company": "xai", "model": "grok-4", "category": "DOCUMENTATION_OR_PRICING_CHANGE",
         "title": "grok-4: old change", "at": "2026-10-01T09:00:00+00:00"},
    ]
    s.save()
    return Store()


# ----------------------------------------------------------------------------------------------- selection

def test_releases_are_selected_once_including_late_detections(week):
    got = {r["model_name"]: late for r, late in w.select_releases(week, NOW)}
    assert got == {"Gemini 5 Pro": False, "Kimi K4": True}
    # GPT-7: outside the window · Grok 6: unconfirmed · Claude Opus 6: already in an earlier report


def test_a_model_recorded_twice_is_counted_once(week):
    dup = release(week, "google", "gemini-5-pro-x", "Gemini 5 Pro (dup)", "2026-11-18", "2026-11-19T10:00:00+00:00")
    week.index["google:gemini-5-pro"] = dup["id"]   # both records claim the same model identity
    week.index["google:gemini-5-pro-x"] = dup["id"]
    names = [r["model_name"] for r, _ in w.select_releases(week, NOW)]
    assert names.count("Gemini 5 Pro (dup)") + names.count("Gemini 5 Pro") == 1


def test_dates_count_by_calendar_day():
    start, end = NOW - timedelta(days=7), NOW
    assert w._in("2026-11-15", start, end)                     # the first day of the window, despite the time of day
    assert not w._in("2026-11-14", start, end)
    assert w._in("2026-11", start, end)


def test_updates_are_grouped_separately_and_deduplicated(week):
    updates = w.select_updates(week, NOW)
    assert set(updates) == {"availability", "retirements", "pricing", "api"}
    assert [x["model"] for x in updates["retirements"]] == ["gpt-5.2"]
    assert len(updates["pricing"]) == 1                        # the same change logged twice is listed once
    assert all("old change" not in x["title"] for g in updates.values() for x in g)


def test_watchlist_holds_unconfirmed_models(week):
    assert [r["model_name"] for r in w.select_watchlist(week, NOW)] == ["Grok 6"]


# ----------------------------------------------------------------------------------------------- benchmarks

def test_lmarena_matching_picks_the_best_variant_and_compares_fairly():
    rows = BENCH["LMArena"]["boards"]["text/overall"]
    best = bm.best_match(rows, COMPANIES["google"], "gemini-5-pro")
    assert best["variant"] == "High" and best["rating"] == 1560
    kimi_new = bm.best_match(rows, COMPANIES["moonshot"], "kimi-k-4")
    kimi_old = bm.best_match(rows, COMPANIES["moonshot"], "kimi-k-3")
    assert "within the confidence intervals" in bm.compare(kimi_new, kimi_old)       # 1460-1480 vs 1470-1479
    gem_old = bm.best_match(rows, COMPANIES["google"], "gemini-4-carbon")
    assert bm.compare(best, gem_old).startswith("higher by 22 points, outside")
    assert bm.compare(best, None) is None


def test_lmarena_reader_finds_one_category_with_few_requests(monkeypatch):
    table = ([lm_row(f"m{i}", 1500 - i, 1, 2, category="overall") for i in range(150)]
             + [lm_row(f"c{i}", 1500 - i, 1, 2, category="coding") for i in range(120)]
             + [lm_row(f"x{i}", 1500 - i, 1, 2, category="math") for i in range(90)])
    calls = []

    class R:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def fake_get(url, params=None, timeout=None):
        calls.append(params["offset"])
        o, n = params["offset"], params["length"]
        return R({"num_rows_total": len(table), "rows": [{"row": r} for r in table[o:o + n]]})
    monkeypatch.setattr("agent.releases.benchmarks.get", fake_get)
    rows = bm.read_category("d", "text", "coding")
    assert [r["model_name"] for r in rows] == [f"c{i}" for i in range(120)]
    assert len(calls) < 20


def test_comparison_uses_history_and_never_mixes_evaluations(week):
    rec = week.history["google:gemini-5-pro"]
    comp = w.comparison_for(week, rec, COMPANIES, BENCH)
    assert comp["rows"]["context"]["new"] == "2M tokens" and comp["rows"]["pricing"]["previous"].startswith("$2 input")
    boards = {x["board"]: x for x in comp["independent"]}
    assert boards["text/overall"]["prev"]["name"] == "Gemini 4 Carbon"
    assert "within the confidence intervals" in boards["text/coding"]["verdict"]
    assert comp["company_benchmarks"][0]["name"] == "SWE-bench Verified"


# ----------------------------------------------------------------------------------------------- landscape & content

def _report(store, config=NO_AI):
    return w.build(store, CFG, config, now=NOW, bench=BENCH)


def test_landscape_is_evidence_only(week):
    rep = _report(week)
    a = rep["answers"]
    assert "Google (Gemini 5 Pro)" in a["improvements"] and "Quiet:" in a["improvements"]
    assert "Gemini 5 Pro (General (text))" in a["improvements"]       # measurably better on LMArena
    assert "coding: 2 of the new models" in a["advancing"]
    assert "Larger context than the predecessor" not in a["advancing"]   # no stored spec diff says so
    assert "no independent results yet" not in a["limitations"]         # both new models have LMArena ratings
    assert "no benchmark numbers in the official sources for kimi k4" in a["limitations"].lower()
    assert "Gemini 5 Pro" not in a["limitations"].split("for ")[1]          # it has a company-reported benchmark
    leaders = [x["company"] for x in rep["facts"]["leaders"]]
    assert leaders[0] == "google" and "anthropic" in leaders


def test_content_without_ai_is_complete_and_ranked(week):
    rep = _report(week)
    c = rep["content"]
    assert len(c["videos"]) == 2 or len(c["videos"]) == 3             # one per release, at most 3
    assert len(c["posts"]) == len(rep["releases"]) and all(len(p["text"]) <= 270 for p in c["posts"])
    assert c["comparison"] and c["education"]
    totals = [v["total"] for v in c["videos"]]
    assert totals == sorted(totals, reverse=True)
    assert all(set(v["scores"]) == {"relevance", "evidence", "education"} for v in c["videos"])


def test_ai_content_with_invented_numbers_is_rejected(week, monkeypatch):
    answer = {"landscape": {"improvements": "Google leads by 90 points.", "advancing": "Coding is advancing.",
                            "converging": "", "limitations": "Benchmarks are self-reported."},
              "videos": [{"title": f"Video {t}", "angle": "a", "why": "w", "research": "r", "releases": [1],
                          "relevance": 5, "education": 4} for t in ("one", "two", "three")],
              "posts": [{"text": "Gemini 5 Pro: 2M context.", "releases": [1], "relevance": 5, "education": 3},
                        {"text": "Kimi K4 is 300% faster!", "releases": [2], "relevance": 5, "education": 3},
                        {"text": "SWE-bench 81.2% for Gemini 5 Pro.", "releases": [1], "relevance": 4, "education": 3}],
              "comparison": {"title": "Gemini 5 Pro vs Gemini 4 Carbon", "angle": "Specs", "why": "w", "releases": [1],
                             "relevance": 4, "education": 4},
              "education": {"title": "How LMArena works", "angle": "Votes", "why": "w", "releases": [1],
                            "relevance": 3, "education": 5}}
    monkeypatch.setattr("agent.releases.weekly.llm.complete", lambda c, s, b: (json.dumps(answer), "fake/ai"))
    rep = _report(week, config={"llm": {}})
    assert "90 points" not in rep["answers"]["improvements"]           # unsupported number: evidence wording kept
    assert rep["answers"]["advancing"] == "Coding is advancing."
    assert [v["title"] for v in rep["content"]["videos"]] == ["Video one", "Video two", "Video three"]
    assert all("300%" not in p["text"] for p in rep["content"]["posts"])   # only 2 valid posts: fallback posts kept
    assert rep["content"]["education"]["title"] == "How LMArena works" and rep["model"] == "fake/ai"


# ----------------------------------------------------------------------------------------------- rendering

def test_report_has_all_six_sections_in_order_valid_html(week):
    rep = _report(week)
    out = w.render(rep, COMPANIES, CFG)
    text = msg.strip_html("\n".join(out))
    heads = ["Weekly Model Intelligence Report", "1. 🚨 NEW MODEL RELEASES", "2. ↔️ MODEL-TO-MODEL COMPARISONS",
             "3. 🔄 IMPORTANT MODEL UPDATES", "4. 🧭 COMPETITIVE LANDSCAPE", "5. 📈 BENCHMARK INTELLIGENCE",
             "6. 💡 CONTENT OPPORTUNITIES"]
    assert [text.index(h) for h in heads] == sorted(text.index(h) for h in heads)
    for s in ("Gemini 5 Pro", "Announced: 18 Nov 2026", "Available: 18 Nov 2026", "Main capabilities:",
              "API / product:", "Official announcement", "Detected late", "Gemini 5 Pro vs Gemini 4 Carbon",
              "📏 Context window: 1M tokens → 2M tokens", "SWE-bench Verified (company-reported): 74.0% → 81.2%",
              "📊 LMArena General (text): 1538 → 1560", "🗑 Retirements and deprecations", "💰 Pricing changes",
              "🟢 Availability changes", "Not confirmed yet", "Grok 6", "Which capabilities are advancing?",
              "Company-reported results", "Independent: LMArena", "never compared with each other",
              "🎬 Video topics (ranked)", "𝕏 Posts (ranked)", "⚖️ Model comparison", "📚 Deeper educational topic",
              "Score "):
        assert s in text, s
    assert "GPT-7" not in text and "Claude Opus 6 ·" not in text
    for m in out:
        assert msg.check_html(m) == [] and msg.tg_len(m) <= 4096


def test_quiet_week_still_reports_clearly(isolated):
    rep = w.build(Store(), CFG, NO_AI, now=NOW, bench={})
    text = msg.strip_html("\n".join(w.render(rep, COMPANIES, CFG)))
    assert "No new model was officially confirmed" in text and "6. 💡" not in text
    assert "LMArena: not available this week" in text


# ----------------------------------------------------------------------------------------------- the weekly run

def test_weekly_models_run_sends_archives_and_never_double_counts(week, isolated, monkeypatch):
    monkeypatch.setattr("agent.releases.weekly.now_utc", lambda: NOW)
    monkeypatch.setattr("agent.releases.weekly.bm.fetch_all", lambda cfg: BENCH)
    monkeypatch.setattr(main, "load", lambda: (NO_AI | {"delivery": {}}, []))
    sent = []
    monkeypatch.setattr(main, "deliver", lambda config, msgs, email, silent=False: (sent.append((msgs, silent)) or
                                                                                      ["telegram"], []))

    class Args:
        dry_run = True
    assert main.cmd_weekly_models(Args()) == 0 and sent == []
    assert not (isolated / "releases" / "weekly").exists()             # dry run: nothing archived
    Args.dry_run = False
    assert main.cmd_weekly_models(Args()) == 0
    messages, silent = sent[0]
    assert "Gemini 5 Pro" in messages[0] and silent is False           # a P0 release: with sound
    assert (isolated / "releases" / "weekly" / "2026-11-22.md").read_text().count("Gemini 5 Pro") >= 1
    assert json.loads((isolated / "state" / "last_model_weekly.json").read_text()) == messages
    assert set(Store().meta["weekly_reported"]) >= {"google:gemini-5-pro", "moonshot:kimi-k-4"}
    # The next run (e.g. a manual one) doesn't count the same releases again.
    assert main.cmd_weekly_models(Args()) == 0
    assert "No new model was officially confirmed" in msg.strip_html("\n".join(sent[1][0]))


def test_weeklymodels_command(monkeypatch, isolated):
    from agent import commands

    monkeypatch.setattr(commands, "LAST_MODEL_WEEKLY", isolated / "m.json")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    class R:
        ok = True

        def json(self):
            return {"result": [{"update_id": 1, "message": {"chat": {"id": 42}, "text": "/weeklymodels"}}]}
    monkeypatch.setattr(commands.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(commands.requests, "post", lambda *a, **k: R())
    sent = []
    monkeypatch.setattr(commands, "send_telegram", lambda msgs: sent.extend(msgs))
    commands.handle({})
    assert "No Weekly Model Intelligence Report yet" in sent[0]
    (isolated / "m.json").write_text(json.dumps(["🧠 report"]))
    commands.handle({})
    assert sent[-1] == "🧠 report"
