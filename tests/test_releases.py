"""Offline tests for the model release monitor (agent/releases/). No network, no Telegram, no API keys:
HTTP and the Telegram Bot API are replaced by fakes, pages come from tests/fixtures/releases/ (trimmed copies of
the real official pages, saved 2026-11-16)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent import deliver
from agent.releases import analyze, classify as cls, config as rconfig, identity, notify, parsers, pipeline, render
from agent.releases.store import Store
from agent.state import State

FIX = Path(__file__).parent / "fixtures" / "releases"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
CFG = rconfig.load()
COMPANIES = CFG["companies"]
SIX = {"anthropic", "openai", "moonshot", "alibaba", "xai", "google"}


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def frozen(monkeypatch, tmp_path):
    """Fixed clock, state in a temp dir, no bot pushes."""
    monkeypatch.setattr("agent.state.ROOT", tmp_path)
    monkeypatch.setattr("agent.releases.pipeline.ROOT", tmp_path)
    for mod in ("agent.releases.pipeline", "agent.releases.store", "agent.state"):
        monkeypatch.setattr(f"{mod}.now_utc", lambda: NOW)
    monkeypatch.setattr("agent.botpush.push", lambda *a, **k: True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


# ----------------------------------------------------------------------------------------------- fakes

class Resp:
    def __init__(self, text="", status=200, data=None):
        self.text, self.status_code, self._data = text, status, data
        self.content = text.encode()

    def json(self):
        return self._data if self._data is not None else json.loads(self.text)


def fake_web(monkeypatch, pages):
    """Every HTTP GET in the release code and the reused news fetchers is answered from `pages` (url -> text)."""
    calls = []

    def get(url, timeout=25, params=None, headers=None, **kw):
        calls.append(url)
        for key, value in pages.items():
            if url == key or url.startswith(key + "?"):
                if isinstance(value, Exception):
                    raise value
                return Resp(value) if isinstance(value, str) else Resp(data=value)
        raise RuntimeError(f"404 for {url}")

    for target in ("agent.releases.fetchers.get", "agent.sources.get", "agent.accuracy.get"):
        monkeypatch.setattr(target, get)
    return calls


class FakeTelegram:
    """Stands in for api.telegram.org. `script` decides each sendMessage answer: "ok", "502", "noid", "400"."""

    def __init__(self, monkeypatch, script=None):
        self.sent, self.edits, self.script, self.next_id = [], [], list(script or []), 500
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:test")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
        monkeypatch.setattr(deliver.requests, "post", self.post)

    def post(self, url, data=None, timeout=None, **kw):
        method = url.rsplit("/", 1)[1]
        if method == "editMessageText":
            self.edits.append(data)
            return Resp(json.dumps({"ok": True, "result": True}))
        outcome = self.script.pop(0) if self.script else "ok"
        if outcome == "502":
            return Resp(json.dumps({"ok": False, "description": "Bad Gateway"}), status=502)
        if outcome == "400":
            return Resp(json.dumps({"ok": False, "description": "Bad Request: chat not found"}), status=400)
        if outcome == "noid":
            return Resp(json.dumps({"ok": True, "result": {}}))
        self.next_id += 1
        self.sent.append({"id": self.next_id, "text": data["text"]})
        return Resp(json.dumps({"ok": True, "result": {"message_id": self.next_id, "date": 1763294400}}))


def obs(company="google", event="listed", model="gemini-5-pro", official=True, source="Gemini API models", **kw):
    o = {"company": company, "source": source, "role": "catalog", "official": official, "event": event,
         "model_ids": [model], "mentions": [], "title": kw.pop("title", f"{source} lists {model}"), "url": None,
         "text": "", "date": None, "tags": [], "status": "active", "specs": {},
         "fingerprints": [kw.pop("fp", f"fp-{source}-{model}")], "uid": f"{source}:{model}"}
    o.update(kw)
    return o


# ----------------------------------------------------------------------------------------------- configuration

def test_all_six_companies_are_configured_and_valid():
    assert rconfig.validate(CFG) == []
    assert set(COMPANIES) == SIX
    for key, c in COMPANIES.items():
        roles = {s["role"] for s in c["sources"]}
        assert roles & {"announcement", "repo"}, key
        assert roles & {"catalog", "changelog"}, key
        for s in c["sources"]:
            assert s.get("verified"), f"{s['name']}: say how the endpoint was verified"
            if s.get("url"):
                assert rconfig.is_official_url(s["url"], c), s["url"]


@pytest.mark.parametrize("company,names", [
    ("anthropic", ["Claude Opus 5.5", "claude-opus-5-5", "claude-3-5-sonnet-20241022", "Claude Fable 5.2"]),
    ("openai", ["GPT-6.1 Terra", "gpt-6-cyber", "gpt-image-2.6-sunburst", "gpt-oss-120b"]),
    ("moonshot", ["Kimi K3.1 Pro", "kimi-k3.1-pro-highspeed", "moonshot-v1-8k"]),
    ("alibaba", ["Qwen4-Max", "qwen3.7-flash-2026-07-15", "qwen-image-2.1-turbo"]),
    ("xai", ["Grok 4.7", "grok-4.20-0309-reasoning", "grok-imagine-video-1.5"]),
    ("google", ["Gemini 4 Hydrogen", "gemini-3.8-flash-lite", "Gemma 4"]),
])
def test_each_company_recognises_its_own_model_names(company, names):
    for name in names:
        assert identity.extract_models(f"Today: {name} is here.", COMPANIES[company]["_patterns"]), (company, name)
    others = [k for k in SIX if k != company]
    for other in others:
        for name in names:
            assert not identity.extract_models(name, COMPANIES[other]["_patterns"]), (other, name)


def test_official_source_identification():
    anthropic = COMPANIES["anthropic"]
    assert rconfig.is_official_url("https://www.anthropic.com/news/x", anthropic)
    assert rconfig.is_official_url("https://platform.claude.com/docs/en/release-notes/feed.xml", anthropic)
    assert not rconfig.is_official_url("https://anthropic.com.evil.example/news", anthropic)
    assert not rconfig.is_official_url("https://www.testingcatalog.com/claude", anthropic)
    assert not rconfig.is_official_url("https://openai.com/news", anthropic)
    bad = json.loads(json.dumps({k: v for k, v in rconfig.load_yaml("releases.yaml").items()}))
    bad["companies"]["anthropic"]["sources"][0]["url"] = "https://www.theverge.com/ai"
    assert any("not one of anthropic's official hosts" in e for e in rconfig.validate(rconfig.load(data=bad)))


def test_third_party_listing_is_never_an_official_release():
    r = cls.classify_group("openai", "gpt-7", [obs("openai", "early_listing", "gpt-7", official=False,
                                                    source="OpenRouter model list (early signal)")],
                           set(), set(), COMPANIES["openai"])
    assert (r["category"], r["verification"]) == (cls.F, cls.UNCONFIRMED)


# ----------------------------------------------------------------------------------------------- identity

@pytest.mark.parametrize("a,b", [
    ("Claude Opus 5.5", "claude-opus-5-5"), ("GPT-6.1 Terra", "gpt-6.1-terra"), ("Kimi K3.1 Pro", "kimi-k3.1-pro"),
    ("kimi-k3.1-pro-highspeed", "kimi-k3.1-pro"), ("grok-4.20-0309-reasoning", "grok-4.20"),
    ("Qwen/Qwen3-235B-A22B-Instruct-2507", "qwen3"), ("qwen3.7-flash-2026-07-15", "qwen3.7-flash"),
    ("anthropic/claude-opus-5-5", "claude-opus-5-5"), ("Gemini 4 Hydrogen", "gemini-4-hydrogen"),
])
def test_same_model_gets_the_same_identity(a, b):
    cfg = COMPANIES["anthropic"] if "claude" in a.lower() else {}
    assert identity.base_key(a, cfg) == identity.base_key(b, cfg)


def test_url_variants_have_one_fingerprint():
    fp = identity.url_fingerprint
    assert fp("https://www.kimi.com/en/blog/kimi-k3-1-pro/") == fp("https://kimi.com/blog/kimi-k3-1-pro")
    assert fp("https://x.ai/news/grok-5?utm_source=x#top") == fp("https://x.ai/news/grok-5")


def test_decimal_versions_follow_lab_conventions():
    assert identity.version_value("4.20") < identity.version_value("4.3") < identity.version_value("4.7")
    assert identity.version_value("5.5") > identity.version_value("5")


# ----------------------------------------------------------------------------------------------- parsers

def test_catalog_parsers_on_real_pages():
    claude = {e["id"]: e for e in parsers.claude_models_md(fixture("claude_models_overview.md"))}
    assert claude["claude-opus-5-5"]["specs"]["context_tokens"] == 1_000_000
    assert claude["claude-opus-5-5"]["specs"]["input_price"] == 4.0
    assert claude["claude-mythos-5-2"]["status"] == "limited"
    openai = {e["id"]: e for e in parsers.openai_models_md(fixture("openai_models_all.md"))}
    assert "gpt-6.1-terra" in openai and openai["gpt-6.1-terra"]["name"] == "GPT-6.1 Terra"
    assert any(e["status"] == "deprecated" for e in openai.values())
    terra = parsers.openai_model_md(fixture("openai_model_gpt-6.1-terra.md"))
    assert terra["context_tokens"] == 1_050_000 and terra["output_price"] == 5.0
    xai = {e["id"]: e for e in parsers.xai_models_md(fixture("xai_models.md"))}
    assert xai["grok-4.7"]["specs"]["context_tokens"] == 500_000 and "grok-4.7 (" not in "".join(xai)
    gemini = {e["id"]: e for e in parsers.gemini_models_md(fixture("gemini_models.md"))}
    assert {"gemini-4-hydrogen", "gemini-4-carbon", "gemini-3.8-flash"} <= set(gemini)
    hydrogen = parsers.gemini_model_md(fixture("gemini_model_gemini-4-hydrogen.md"))
    assert hydrogen["context_tokens"] == 1_048_576 and hydrogen["max_output_tokens"] == 65_536
    kimi = {e["id"]: e for e in parsers.kimi_models_md(fixture("kimi_models.md"))}
    assert kimi["kimi-k3.1-pro"]["status"] == "active" and kimi["kimi-k2.5"]["status"] == "deprecated"


def test_alibaba_parser_keeps_only_qwen_models():
    page = """<main><p>Qwen4-Max is the next-generation flagship multimodal model in the Qwen family.</p>
      <h4>pixverse/pixverse-v6-t2v</h4><p>PixVerse V6 text-to-video model.</p>
      <h4>qwen-image-2.1-turbo</h4><p>Qwen-Image-2.1-Turbo is an open-source image model.</p>
      <h4>deepseek-v4.1-flash-1017</h4><p>DeepSeek model.</p>
      <h4>qwen4-max-preview</h4><p>Currently in preview.</p></main>"""
    got = {e["id"]: e for e in parsers.alibaba_new_models(page, COMPANIES["alibaba"])}
    assert set(got) == {"Qwen4-Max", "qwen-image-2.1-turbo", "qwen4-max-preview"}
    assert got["qwen4-max-preview"]["status"] == "preview"


def test_changelog_parsers_on_real_pages():
    oa = parsers.openai_changelog_md(fixture("openai_changelog.md"), COMPANIES["openai"])
    cyber = next(e for e in oa if e["model_ids"] == ["gpt-6-cyber"])
    assert cyber["date"] == "2026-11-05" and "feature_tag" in cyber["tags"]
    gm = parsers.gemini_changelog_md(fixture("gemini_changelog.md"), COMPANIES["google"])
    hyd = next(e for e in gm if "gemini-4-hydrogen" in e["model_ids"])
    assert hyd["date"] == "2026-11-12" and "release" in hyd["tags"] and "Gemini 3.8 Flash" in hyd["mentions"]
    xr = parsers.xai_release_notes_md(fixture("xai_release_notes.md"), COMPANIES["xai"], today=NOW.date())
    grok = next(e for e in xr if e["title"] == "Grok 4.7")
    assert grok["date"] == "2026-09" and grok["model_ids"]


def test_comparisons_in_a_note_are_not_its_subject():
    text = "Both models require 30-day retention, like Claude Fable 5.1 and Claude Mythos 5.1."
    assert parsers.subject_models(text, COMPANIES["anthropic"]["_patterns"]) == []
    text = "Claude Fable 5.2 is 30% faster than Claude Fable 5.1."
    assert parsers.subject_models(text, COMPANIES["anthropic"]["_patterns"]) == ["Claude Fable 5.2"]


@pytest.mark.parametrize("parser", ["claude_models_md", "openai_models_md", "xai_models_md", "gemini_models_md",
                                    "kimi_models_md", "alibaba_new_models"])
def test_malformed_pages_give_no_entries(parser):
    for junk in ("", "<html><body>Access denied</body></html>", "| a | b |\n|---|---|\n| x | y |", "{{{"):
        assert parsers.CATALOG_PARSERS[parser](junk, COMPANIES["alibaba"]) == []


def test_a_page_that_suddenly_parses_to_nothing_is_a_failure(monkeypatch):
    fake_web(monkeypatch, {"https://platform.kimi.ai/docs/models.md": "<html>Please enable JavaScript</html>"})
    src = next(s for s in COMPANIES["moonshot"]["sources"] if s["name"] == "Kimi API models")
    from agent.releases.fetchers import fetch_source
    with pytest.raises(ValueError, match="found nothing"):
        fetch_source(src, COMPANIES["moonshot"])


# ----------------------------------------------------------------------------------------------- classification

def _classify(observations, known=(), exact=(), company="google", existing=None, base="gemini-5-pro"):
    return cls.classify_group(company, base, observations, set(known), set(exact), COMPANIES[company], existing)


def test_new_model_from_official_listing_and_changelog_is_confirmed():
    r = _classify([obs(), obs(event="changelog", source="Gemini API release notes", tags=["release"])])
    assert (r["category"], r["verification"]) == (cls.A, cls.CONFIRMED)


def test_preview_only_listing_needs_manual_review():
    r = _classify([obs(status="preview", tags=["preview"])])
    assert (r["category"], r["verification"]) == (cls.A, cls.REVIEW)


def test_unknown_model_merely_mentioned_needs_manual_review():
    r = _classify([obs(event="announcement", source="Google Gemini blog", title="How teams use Gemini 5 Pro")])
    assert r["verification"] == cls.REVIEW


def test_new_snapshot_of_known_model_is_an_upgrade_not_a_new_model():
    r = _classify([obs(model="gemini-5-pro-preview-06-2027")], known={"gemini-5-pro"}, exact={"gemini-5-pro"})
    assert (r["category"], r["verification"]) == (cls.B, cls.UPDATE)


def test_availability_change_of_known_model():
    r = _classify([obs(event="status_change", tags=["availability"], status="active")], known={"gemini-5-pro"},
                  exact={"gemini-5-pro"})
    assert r["category"] == cls.C


@pytest.mark.parametrize("o", [
    obs(event="spec_change", tags=["pricing"]),                                       # a price edit
    obs(event="status_change", tags=["deprecation"], status="deprecated"),            # a deprecation
    obs(event="changelog", source="Gemini API release notes", tags=["deprecation"]),  # a retirement note
])
def test_documentation_changes_never_become_new_model_alerts(o):
    r = _classify([o], known={"gemini-5-pro"}, exact={"gemini-5-pro"})
    assert r["category"] == cls.D
    assert not analyze.is_notifiable(r["category"], r["verification"]) or r["category"] != cls.A


def test_feature_post_about_known_model_is_a_feature_announcement():
    r = _classify([obs(event="announcement", source="Google Gemini blog", title="Gemini 5 Pro in Google Sheets")],
                  known={"gemini-5-pro"}, exact={"gemini-5-pro"})
    assert r["category"] == cls.E


def test_official_confirmation_overrides_earlier_unconfirmed_signal():
    existing = {"verification": cls.UNCONFIRMED, "fingerprints": []}
    r = _classify([obs(event="changelog", source="Gemini API release notes", tags=["release"])],
                  known={"gemini-5-pro"}, exact={"gemini-5-pro"}, existing=existing)
    assert (r["category"], r["verification"]) == (cls.A, cls.CONFIRMED)


def test_already_recorded_evidence_is_rejected_as_duplicate():
    existing = {"verification": cls.CONFIRMED, "classification": cls.A, "fingerprints": ["fp-Gemini API models-gemini-5-pro"]}
    r = _classify([obs()], known={"gemini-5-pro"}, existing=existing)
    assert r["verification"] == cls.DUPLICATE


def test_one_launch_with_two_models_becomes_one_release():
    shared = dict(event="changelog", source="Claude Platform release notes", tags=["release"], fp="entry-1")
    results = [_classify([obs("anthropic", model="claude-fable-6", **shared)], company="anthropic", base="claude-fable-6"),
               _classify([obs("anthropic", model="claude-mythos-6", **shared)], company="anthropic", base="claude-mythos-6")]
    assert len(cls.merge_launches(results)) == 1


# ----------------------------------------------------------------------------------------------- predecessor

@pytest.fixture
def registry_store():
    s = Store("test-registry")
    for company, ids in {"openai": ["gpt-5.6-cyber", "gpt-5.5", "gpt-6-luna"],
                         "xai": ["grok-4.3", "grok-4.20-0309-reasoning", "grok-4.6"],
                         "moonshot": ["kimi-k3", "kimi-k2.6"],
                         "google": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.1-flash-lite"]}.items():
        for mid in ids:
            s.learn_model(company, {"id": mid, "name": mid, "status": "active", "specs": {}}, "test")
    return s


def test_predecessor_named_by_the_company_wins(registry_store):
    texts = [{"text": "See What's new for API changes and migration guidance from Gemini 3.8 Flash."}]
    p = analyze.find_predecessor("google", COMPANIES["google"], ["gemini-4-hydrogen"], texts, registry_store)
    assert p["id"] == "gemini-3.8-flash" and p["method"].startswith("named by the company")


def test_predecessor_by_lineage_same_family_and_tier(registry_store):
    p = analyze.find_predecessor("openai", COMPANIES["openai"], ["gpt-6-cyber"], [], registry_store)
    assert p["id"] == "gpt-5.6-cyber" and "same family and tier" in p["method"]


def test_predecessor_does_not_assume_the_biggest_number(registry_store):
    # xAI's 4.20 came before 4.3: the predecessor of grok-4.5 is grok-4.3, not "4.20".
    p = analyze.find_predecessor("xai", COMPANIES["xai"], ["grok-4.5"], [], registry_store)
    assert p["id"] == "grok-4.3"


def test_predecessor_falls_back_to_family_then_gives_up(registry_store):
    p = analyze.find_predecessor("moonshot", COMPANIES["moonshot"], ["kimi-k3.1-pro"], [], registry_store)
    assert p["id"] == "kimi-k3" and "lower confidence" in p["method"]
    assert analyze.find_predecessor("google", COMPANIES["google"], ["gemma-9"], [], registry_store) is None


# ----------------------------------------------------------------------------------------------- comparison & AI checks

def test_spec_diffs_report_improvements_and_regressions():
    better, worse, same = analyze.spec_diffs(
        {"context_tokens": 400_000, "input_price": 12.5, "output_price": 75, "max_output_tokens": 128_000},
        {"context_tokens": 1_050_000, "input_price": 20, "output_price": 100, "max_output_tokens": 128_000})
    assert better == ["Context window grows from 400K tokens to 1.05M tokens"]
    assert worse[0].startswith("Input price rises from $12.5 to $20") and "+60%" in worse[0]
    assert same == ["Output limit unchanged (128K tokens)"]


def test_ai_output_without_verbatim_evidence_is_discarded():
    sources = [{"text": "Gemini 5 Pro scores 81.2% on SWE-bench Verified, up from 74.0% for Gemini 4 Carbon."}]
    corpus = sources[0]["text"]
    data = {"benchmarks": [
        {"name": "SWE-bench Verified", "new": "81.2%", "previous": "74.0%", "source": 1,
         "quote": "scores 81.2% on SWE-bench Verified, up from 74.0%"},
        {"name": "MMLU", "new": "95%", "previous": "", "source": 1, "quote": "scores 95% on MMLU"},       # not in text
        {"name": "AIME", "new": "99.9%", "previous": "", "source": 1, "quote": "scores 81.2% on SWE-bench Verified"},
    ], "interpretation": ["Coding improves by 7.2 points.", "It is 40% faster."],
        "whats_new": [{"text": "Better coding", "source": 2, "quote": "anything"}], "is_new_model": "no"}
    clean, dropped = analyze.validate_ai(data, sources, corpus)
    assert [b["name"] for b in clean["benchmarks"]] == ["SWE-bench Verified"]
    assert clean["interpretation"] == []          # 7.2 and 40 appear nowhere in the sources
    assert clean["whats_new"] == [] and dropped == 5
    assert clean["is_new_model"] == "unclear"     # a "no" without a quote doesn't count


def _facts(**over):
    f = {"company": "google", "bases": ["gemini-5-pro"], "model_ids": ["gemini-5-pro"], "model_name": "Gemini 5 Pro",
         "category": cls.A, "verification": cls.CONFIRMED, "reasons": ["introduced by: Gemini API release notes"],
         "announcement_date": "2026-11-12", "availability_date": None, "availability_status": "Preview",
         "api_access": "Yes: `gemini-5-pro`", "official": [], "specs": {}, "previous_specs": {},
         "texts": [{"kind": "announcement", "source": "Google DeepMind blog", "url": "https://deepmind.google/blog/x"}],
         "previous": {"name": "Gemini 4 Carbon", "id": "gemini-4-carbon", "method": "named by the company"},
         "comparison": {"context": {"previous": "1M tokens", "new": "2M tokens", "basis": "official specs", "quote": ""}},
         "improved": ["Context window grows from 1M tokens to 2M tokens"], "regressed": [], "unchanged": [],
         "whats_new": ["Gemini 5 Pro <b>doubles</b> the context window."], "ai_model": None, "ai_dropped": 0,
         "ai": {"benchmarks": [{"name": "SWE-bench", "new": "81%", "previous": "74%"}]}}
    f.update(over)
    return f


def test_release_message_has_every_section_in_order_and_fits_a_phone():
    raw = "\n".join(render.render_release(_facts(), COMPANIES))
    text = render.plain([raw])
    order = ["NEW AI MODEL RELEASE", "COMPANY:", "NEW MODEL:", "RELEASE INFORMATION:", "Announcement date:",
             "Availability date:", "Model category:", "Availability status:", "API access:", "WHAT'S NEW:",
             "COMPARISON WITH PREVIOUS MODEL:", "Previous model: Gemini 4 Carbon", "WHAT ACTUALLY IMPROVED:",
             "WHAT HAS NOT BEEN ESTABLISHED:", "OFFICIAL SOURCES:", "Original announcement", "Official documentation",
             "Model information", "WHY THIS MATTERS:", "CONTENT OPPORTUNITIES:", "1. News video",
             "2. Educational explainer", "3. Model comparison video", "VERIFICATION:\nOFFICIAL CONFIRMED",
             "DELIVERY:\nPending Telegram confirmation."]
    positions = [text.index(s) for s in order]
    assert positions == sorted(positions)
    assert "Company-reported" in text and "Independent: none collected" in text
    assert "SWE-bench: 81% vs 74% before" in text
    assert "&lt;b&gt;doubles" in raw                       # source text is escaped, never trusted as HTML
    assert "Availability date: Not stated" in text and "The availability date" in text
    assert "|" not in raw                                   # grouped sections, no wide tables
    assert all(len(m) <= 4096 for m in render.render_release(_facts(), COMPANIES))


def test_missing_predecessor_is_said_plainly():
    text = "\n".join(render.render_release(_facts(previous=None, comparison={}, improved=[]), COMPANIES))
    assert "Previous model: <i>not established</i>" in text and "Which model this replaces" in text


# ----------------------------------------------------------------------------------------------- Telegram delivery

def test_send_telegram_message_returns_telegrams_confirmation(monkeypatch):
    tg = FakeTelegram(monkeypatch)
    assert deliver.send_telegram_message("hi")["message_id"] == 501
    assert deliver.send_telegram(["a", "b"]) == [502, 503]           # the old API still works and now returns ids
    assert [m["text"] for m in tg.sent] == ["hi", "a", "b"]


@pytest.mark.parametrize("answer", ["502", "400", "noid"])
def test_send_telegram_message_raises_unless_telegram_confirms(monkeypatch, answer):
    FakeTelegram(monkeypatch, [answer])
    with pytest.raises(deliver.TelegramError):
        deliver.send_telegram_message("hi")


def _queued(store, parts=("part one", "part two\n<b>DELIVERY:</b>\nPending Telegram confirmation."),
            category=cls.A):
    rec = store.new_release("google", ["gemini-5-pro"], model_name="Gemini 5 Pro", verification=cls.CONFIRMED)
    nid = store.enqueue(rec["id"], "release", category, cls.CONFIRMED, "Gemini 5 Pro", list(parts))
    return rec, store.outbox[nid]


def test_confirmed_delivery_records_ids_time_and_sends_receipt(monkeypatch):
    tg, store = FakeTelegram(monkeypatch), Store()
    rec, n = _queued(store)
    summary = notify.process_outbox(store, CFG["settings"], store.save)
    assert summary["SENT"] == 1 and n["status"] == "SENT"
    assert rec["delivery"]["status"] == "SENT" and rec["delivery"]["message_ids"] == [501, 502]
    assert rec["delivery"]["sent_at"] and rec["verification"] == cls.CONFIRMED
    assert "Delivery receipt" in tg.sent[-1]["text"] and "#501, #502" in tg.sent[-1]["text"]
    assert rec["delivery"]["receipt"]["status"] == "SENT"
    assert tg.edits and "SENT. Telegram confirmed message #502" in tg.edits[0]["text"]


def test_failed_delivery_stays_queued_and_keeps_verification_separate(monkeypatch):
    FakeTelegram(monkeypatch, ["502"])
    store = Store()
    rec, n = _queued(store)
    notify.process_outbox(store, CFG["settings"], store.save)
    assert n["status"] == "RETRY_PENDING" and rec["delivery"]["status"] == "RETRY_PENDING"
    assert rec["delivery"]["message_ids"] == [] and "502" in rec["delivery"]["last_error"]
    assert rec["verification"] == cls.CONFIRMED


def test_retry_sends_only_the_parts_telegram_did_not_confirm(monkeypatch):
    tg = FakeTelegram(monkeypatch, ["ok", "502"])          # part 1 delivered, part 2 failed
    store = Store()
    rec, n = _queued(store)
    notify.process_outbox(store, CFG["settings"], store.save)
    assert n["status"] == "RETRY_PENDING" and list(n["sent_parts"]) == ["0"]
    notify.process_outbox(store, CFG["settings"], store.save)  # next run
    texts = [m["text"] for m in tg.sent]
    assert texts.count("part one") == 1                        # never sent twice
    assert n["status"] == "SENT" and rec["delivery"]["message_ids"] == [501, 502]


def test_delivery_gives_up_after_max_attempts(monkeypatch):
    FakeTelegram(monkeypatch, ["502"] * 10)
    store = Store()
    rec, n = _queued(store)
    settings = {**CFG["settings"], "max_delivery_attempts": 3}
    for _ in range(3):
        notify.process_outbox(store, settings, store.save)
    assert n["status"] == "FAILED" and n["attempts"] == 3
    notify.process_outbox(store, settings, store.save)
    assert n["attempts"] == 3                                  # no more tries once FAILED


def test_without_telegram_the_alert_waits_as_pending(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    store = Store()
    rec, n = _queued(store)
    notify.process_outbox(store, CFG["settings"], store.save)
    assert n["status"] == "PENDING" and n["attempts"] == 0


def test_identical_alert_is_never_queued_twice():
    store = Store()
    rec, n = _queued(store)
    again = store.enqueue(rec["id"], "release", cls.A, cls.CONFIRMED, "Gemini 5 Pro", list(n["parts"]))
    assert again == n["id"] and len(store.outbox) == 1


# ----------------------------------------------------------------------------------------------- persistence

def test_release_history_persists_and_is_never_pruned(frozen):
    store = Store()
    rec, n = _queued(store)
    rec["first_detected"] = "2025-01-01T00:00:00+00:00"      # far older than the 30-day memory of other runs
    store.save()
    again = Store()
    assert again.history[rec["id"]]["model_name"] == "Gemini 5 Pro"
    assert again.index["google:gemini-5-pro"] == rec["id"] and n["id"] in again.outbox
    saved = json.loads((frozen / "state" / "releases.json").read_text())
    assert {"registry", "history", "index", "outbox", "sources", "failures"} <= set(saved)


def test_existing_state_files_keep_working_with_extra_sections(frozen):
    s = State("digest")
    s.observe("item-1")
    s.extra["custom"] = {"kept": True}
    s.save()
    s2 = State("digest")
    assert "item-1" in s2.seen and s2.extra["custom"] == {"kept": True}


# ----------------------------------------------------------------------------------------------- end to end

DEEPMIND_RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>DeepMind</title>{items}</channel></rss>"""
HYDROGEN_ITEM = ("<item><title>Introducing Gemini 4 Hydrogen</title>"
                 "<link>https://deepmind.google/blog/introducing-gemini-4-hydrogen/</link>"
                 "<guid>https://deepmind.google/blog/introducing-gemini-4-hydrogen/</guid>"
                 "<pubDate>Thu, 12 Nov 2026 18:00:50 +0000</pubDate><description>Fast frontier model.</description></item>")
OLD_ITEM = ("<item><title>Our work on weather models</title><link>https://deepmind.google/blog/weather/</link>"
            "<guid>weather</guid><pubDate>Mon, 02 Nov 2026 10:00:00 +0000</pubDate><description>x</description></item>")
ARTICLE = """<html><head><meta property="og:title" content="Introducing Gemini 4 Hydrogen"></head><body><article>
<time datetime="2026-11-12">Nov 12, 2026</time>
<p>Today, we're introducing Gemini 4 Hydrogen, bringing cutting-edge intelligence to everyone with speed and efficiency.</p>
<p>Gemini 4 Hydrogen is a major standalone upgrade for teams migrating from Gemini 3.8 Flash, priced at $1.50 per million input tokens.</p>
</article></body></html>"""


def _google_only_cfg():
    data = rconfig.load_yaml("releases.yaml")
    google = data["companies"]["google"]
    keep = {"Google DeepMind blog", "Gemini API release notes", "Gemini API models"}
    google["sources"] = [s for s in google["sources"] if s["name"] in keep]
    return rconfig.load(data={"settings": data["settings"], "companies": {"google": google}, "early_signals": []})


def _without_hydrogen(text):
    lines = text.splitlines()
    return "\n".join(l for l in lines if "hydrogen" not in l.lower())


def _pages(with_hydrogen):
    models, changelog = fixture("gemini_models.md"), fixture("gemini_changelog.md")
    if not with_hydrogen:
        models = _without_hydrogen(models)
        changelog = changelog[: changelog.index("## November 12, 2026")] + changelog[changelog.index("## November 11, 2026"):]
    return {
        "https://deepmind.google/blog/rss.xml": DEEPMIND_RSS.format(items=(HYDROGEN_ITEM if with_hydrogen else "") + OLD_ITEM),
        "https://aistudio.google.com/docs/changelog.md": changelog,
        "https://aistudio.google.com/docs/models.md": models,
        "https://aistudio.google.com/docs/models/gemini-4-hydrogen.md": fixture("gemini_model_gemini-4-hydrogen.md"),
        "https://deepmind.google/blog/introducing-gemini-4-hydrogen/": ARTICLE,
    }


NO_AI = {"llm": {"order": [], "providers": {}}}


def test_end_to_end_new_model_alert_then_no_duplicates(monkeypatch, frozen):
    cfg = _google_only_cfg()
    tg = FakeTelegram(monkeypatch)
    # Run 1: the first look learns everything silently.
    fake_web(monkeypatch, _pages(with_hydrogen=False))
    assert pipeline.run(cfg=cfg, config=NO_AI, store=Store()) == 0
    assert tg.sent == []
    # Run 2: Google publishes Gemini 4 Hydrogen on its blog, in the changelog and in the model list.
    fake_web(monkeypatch, _pages(with_hydrogen=True))
    assert pipeline.run(cfg=cfg, config=NO_AI, store=Store()) == 0
    alert = "\n".join(m["text"] for m in tg.sent if "NEW AI MODEL RELEASE" in m["text"] or "DELIVERY:" in m["text"])
    assert "Gemini 4 Hydrogen" in alert and "OFFICIAL CONFIRMED" in alert
    assert "Previous model: Gemini 3.8 Flash" in alert and "migrating from Gemini 3.8 Flash" in alert
    assert "Announcement date: 12 November 2026" in alert and "1,048,576 tokens" in alert
    assert sum("NEW AI MODEL RELEASE" in m["text"] for m in tg.sent) == 1     # blog + changelog + catalog = 1 alert
    assert any("Delivery receipt" in m["text"] for m in tg.sent)
    store = Store()
    rec = store.history["google:gemini-4-hydrogen"]
    assert rec["verification"] == cls.CONFIRMED and rec["delivery"]["status"] == "SENT"
    assert rec["delivery"]["message_ids"] and rec["previous_model"]["name"] == "Gemini 3.8 Flash"
    assert rec["announcement_date"] == "2026-11-12" and rec["source_url"].startswith("https://deepmind.google/")
    assert (frozen / "releases" / "2026-11-12_google_gemini-4-hydrogen.md").exists()
    # Run 3: nothing new → no Telegram message at all.
    before = len(tg.sent)
    assert pipeline.run(cfg=cfg, config=NO_AI, store=Store()) == 0
    assert len(tg.sent) == before


def test_end_to_end_docs_edit_does_not_alert(monkeypatch, frozen):
    cfg = _google_only_cfg()
    tg = FakeTelegram(monkeypatch)
    pages = _pages(with_hydrogen=True)
    fake_web(monkeypatch, pages)
    pipeline.run(cfg=cfg, config=NO_AI, store=Store(), bootstrap=True)
    edited = dict(pages)
    edited["https://aistudio.google.com/docs/models.md"] = pages["https://aistudio.google.com/docs/models.md"].replace(
        "Our previous-generation Flash model for software engineering", "Our updated Flash model for engineering")
    fake_web(monkeypatch, edited)
    pipeline.run(cfg=cfg, config=NO_AI, store=Store())
    assert tg.sent == []
    assert Store().changes and Store().changes[0]["category"] == cls.D


def test_end_to_end_ai_reading_is_validated(monkeypatch, frozen):
    cfg = _google_only_cfg()
    tg = FakeTelegram(monkeypatch)
    fake_web(monkeypatch, _pages(with_hydrogen=False))
    pipeline.run(cfg=cfg, config=NO_AI, store=Store())
    answer = {"model_name": "Gemini 4 Hydrogen", "is_new_model": "yes",
              "predecessor": {"name": "Gemini 3.8 Flash", "source": 1, "quote": "migrating from Gemini 3.8 Flash"},
              "benchmarks": [{"name": "SWE-bench", "new": "90%", "previous": "", "source": 1, "quote": "invented"}],
              "comparison": {"pricing": {"new": "$1.50 per million input tokens", "previous": "", "source": 1,
                                         "quote": "priced at $1.50 per million input tokens"}},
              "why_it_matters": "A cheap fast model changes defaults for builders.",
              "content_ideas": {"news_video": "Gemini 4 Hydrogen explained for builders",
                                "explainer": "Hydrogen in 60 seconds"}}
    monkeypatch.setattr("agent.releases.analyze.llm.complete", lambda c, s, build: (json.dumps(answer), "fake/ai"))
    fake_web(monkeypatch, _pages(with_hydrogen=True))
    pipeline.run(cfg=cfg, config=NO_AI, store=Store())
    alert = "\n".join(m["text"] for m in tg.sent)
    assert "Gemini 4 Hydrogen explained for builders" in alert and "A cheap fast model" in alert
    assert "60 seconds" not in alert                # an idea with a number the sources don't contain is dropped
    assert "$1.50 per million input tokens" in alert
    assert "90%" not in alert                       # the unsupported benchmark never reaches you
    assert "1 unsupported AI statements discarded" in alert


def test_end_to_end_unreadable_announcement_is_not_confirmed(monkeypatch, frozen):
    cfg = _google_only_cfg()
    data = rconfig.load_yaml("releases.yaml")
    google = data["companies"]["google"]
    google["sources"] = [s for s in google["sources"] if s["name"] == "Google DeepMind blog"] + [
        {"name": "Gemini API models", "role": "catalog", "type": "markdown", "parser": "gemini_models_md",
         "url": "https://aistudio.google.com/docs/models.md", "verified": "test"}]
    cfg = rconfig.load(data={"settings": data["settings"], "companies": {"google": google}, "early_signals": []})
    tg = FakeTelegram(monkeypatch)
    pages = _pages(with_hydrogen=False)
    fake_web(monkeypatch, pages)
    pipeline.run(cfg=cfg, config=NO_AI, store=Store())
    later = _pages(with_hydrogen=False)
    later["https://deepmind.google/blog/rss.xml"] = DEEPMIND_RSS.format(items=HYDROGEN_ITEM + OLD_ITEM)
    later["https://deepmind.google/blog/introducing-gemini-4-hydrogen/"] = RuntimeError("403 Forbidden")
    fake_web(monkeypatch, later)
    pipeline.run(cfg=cfg, config=NO_AI, store=Store())
    text = "\n".join(m["text"] for m in tg.sent)
    assert "NEW AI MODEL RELEASE" not in text and "REQUIRES MANUAL REVIEW" in text


def test_broken_official_source_is_reported_once(monkeypatch, frozen):
    cfg = _google_only_cfg()
    tg = FakeTelegram(monkeypatch)
    pages = _pages(with_hydrogen=False)
    pages["https://aistudio.google.com/docs/changelog.md"] = RuntimeError("HTTP 500")
    fake_web(monkeypatch, pages)
    for _ in range(4):
        pipeline.run(cfg=cfg, config=NO_AI, store=Store())
    health = [m for m in tg.sent if "Release monitor" in m["text"]]
    assert len(health) == 1 and "Gemini API release notes" in health[0]["text"]


def test_dry_run_sends_and_saves_nothing(monkeypatch, frozen):
    cfg = _google_only_cfg()
    tg = FakeTelegram(monkeypatch)
    fake_web(monkeypatch, _pages(with_hydrogen=True))
    assert pipeline.run(cfg=cfg, config=NO_AI, store=Store(), dry_run=True) == 0
    assert tg.sent == [] and not (frozen / "state" / "releases.json").exists()


def test_releases_command_lists_recent_releases(monkeypatch):
    from agent import commands
    store = Store()
    rec, n = _queued(store)
    rec.update(classification=cls.A, announcement_date="2026-11-12", source_url="https://deepmind.google/blog/x")
    rec["delivery"]["status"] = "SENT"
    store.save()
    text = "\n".join(commands.recent_releases())
    assert "Gemini 5 Pro" in text and "OFFICIAL CONFIRMED" in text and "delivery SENT" in text
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(commands.requests, "post", lambda *a, **k: type("R", (), {"ok": True})())
    monkeypatch.setattr(commands.requests, "get", lambda *a, **k: type("R", (), {"json": lambda self: {"result": [
        {"update_id": 1, "message": {"chat": {"id": 42}, "text": "/releases"}}]}})())
    monkeypatch.setattr(commands, "send_telegram", lambda msgs: sent.extend(msgs))
    commands.handle({})
    assert any("Gemini 5 Pro" in m for m in sent)


def test_feed_item_linking_off_site_is_not_official(monkeypatch, frozen):
    cfg = _google_only_cfg()
    FakeTelegram(monkeypatch)
    fake_web(monkeypatch, _pages(with_hydrogen=False))
    store = Store()
    pipeline.run(cfg=cfg, config=NO_AI, store=store)
    item = HYDROGEN_ITEM.replace("https://deepmind.google/blog/introducing-gemini-4-hydrogen/",
                                 "https://rumours.example.com/gemini-4-hydrogen")
    pages = _pages(with_hydrogen=False)
    pages["https://deepmind.google/blog/rss.xml"] = DEEPMIND_RSS.format(items=item + OLD_ITEM)
    fake_web(monkeypatch, pages)
    store = Store()
    source = next(s for s in cfg["companies"]["google"]["sources"] if s["name"] == "Google DeepMind blog")
    from agent.releases.fetchers import fetch_source
    got = pipeline.observe(source, cfg["companies"]["google"], fetch_source(source, cfg["companies"]["google"]),
                           store, first_run=False)
    assert got and got[0]["official"] is False
