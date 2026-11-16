"""Offline tests for the Telegram message system (agent/messages.py) and everything that now uses it: sections,
priorities, formatting, HTML safety, message splitting, duplicate prevention and the new commands."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import agent.__main__ as main
from agent import alerts, deliver, digest, ledger as led, messages as msg
from agent.common import load_yaml, make_item, now_utc
from agent.releases import analyze, config as rconfig, render as rrender
from agent.releases.store import Store

CONFIG = load_yaml("config.yaml")
COMPANIES = CONFIG["companies"]
RCFG = rconfig.load()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.state.ROOT", tmp_path)
    monkeypatch.setattr("agent.botpush.push", lambda *a, **k: True)
    return tmp_path


def item(title, source="Test Feed", tier="newsletter", company=None, hours_ago=1, url=None, summary=""):
    src = {"name": source, "tier": tier, **({"company": company} if company else {})}
    return make_item(src, title, url or f"https://example.com/{abs(hash(title))}", summary,
                     now_utc() - timedelta(hours=hours_ago))


def assert_valid(messages):
    for m in messages:
        assert msg.check_html(m) == [], (msg.check_html(m), m[:300])
        assert msg.tg_len(m) <= 4096


# ----------------------------------------------------------------------------------------------- cards & structure

def test_card_has_every_standard_field_in_order():
    c = msg.card("new_models", "P0", "Gemini 5 Pro launches", company="Google", speaker="Demis Hassabis",
                 what="Google released Gemini 5 Pro.", why="A new flagship changes defaults.",
                 details=["2M context", "$2 per 1M input"], sources=[("DeepMind", "https://deepmind.google/x")],
                 date="2026-11-12", verification="✅ Official", explain={"introduced": "A model", "who": "Developers"})
    text = msg.render_card(c, 1)
    order = ["1. Gemini 5 Pro launches", "🔴 P0 Critical", "✅ Official", "📅 12 Nov 2026", "🏢 Google",
             "🗣 Demis Hassabis", "What happened:", "Why it matters:", "Details:", "📘 Explained",
             "What was introduced:", "Who benefits:", "🔗"]
    positions = [text.index(s) for s in order]
    assert positions == sorted(positions)
    assert "<blockquote expandable>" in text and msg.check_html(text) == []


def test_compact_card_stays_short_and_skips_the_explanation():
    c = msg.card("industry", "P2", "x", what="w " * 500, why="y " * 300, details=list("abcdef"),
                 explain={"introduced": "i"})
    text = msg.render_card(c, compact=True)
    assert "Explained" not in text and len(text) < 1000


def test_html_is_escaped_everywhere():
    c = msg.card("industry", "P2", "<script>alert(1)</script> & co", company="A<b>", what="x < y & z",
                 sources=[("q\"name", 'https://e.com/?a=1&b="2"')], details=["<i>"])
    text = msg.render_card(c)
    assert "<script>" not in text and "&lt;script&gt;" in text and "a=1&amp;b=&quot;2&quot;" in text
    assert msg.check_html(text) == []


def test_only_sections_with_content_appear_in_the_standard_order_sorted_by_priority():
    cards = [msg.card("tools", "P3", "repo"), msg.card("new_models", "P1", "model B"),
             msg.card("new_models", "P0", "model A"), msg.card("podcasts", "P2", "talk")]
    text = "\n".join(msg.report("Report", cards, health=["a source is failing"],
                                content=[{"kind": "news", "title": "Video"}]))
    heads = ["🚨 NEW MODEL RELEASES", "🛠 NEW AI TOOLS AND PROJECTS", "🎙 AI PODCASTS AND INTERVIEWS",
             "💡 CONTENT OPPORTUNITIES", "⚙️ AGENT HEALTH AND SYSTEM NOTIFICATIONS"]
    assert [text.index(h) for h in heads] == sorted(text.index(h) for h in heads)
    assert "🧠 AI RESEARCH" not in text and "📰 AI INDUSTRY NEWS" not in text
    assert text.index("model A") < text.index("model B")       # P0 before P1 inside a section
    assert "🔴 1 P0 · 🟠 1 P1 · 🔵 1 P2 · ⚪ 1 P3" in text


def test_content_opportunities_have_title_angle_why_and_research():
    raw = [{"kind": "news", "title": "T1", "angle": "A1", "why": "W1", "research": "R1"},
           {"kind": "B", "title": "T2", "angle": "A2"}, {"kind": "podcast", "title": "nope"},
           {"kind": "analysis", "title": "Is it 10x faster?", "research": "benchmark it"}]
    ideas = msg.clean_opportunities(raw, check=lambda v: "10x" not in v)
    assert [o["kind"] for o in ideas] == ["news", "education"]   # unknown kind and an unsupported claim dropped
    text = msg.content_block(ideas, "Gemini 5 Pro")
    for s in ("A. NEWS: T1", "Angle:", "Why it's interesting:", "Research or testing needed:", "B. EDUCATION: T2",
              "You decide"):
        assert s in text
    assert msg.check_html(text) == []


# ----------------------------------------------------------------------------------------------- splitting

def test_long_reports_split_in_order_with_valid_html_and_page_markers():
    cards = [msg.card("industry", "P2", f"Story {i}", what="detail " * 120, explain={"does": "explains " * 40},
                      sources=[("s", f"https://e.com/{i}")]) for i in range(25)]
    out = msg.report("Brief", cards)
    assert len(out) > 2
    assert_valid(out)
    joined = "\n".join(out)
    assert [joined.index(f"Story {i}") for i in range(25)] == sorted(joined.index(f"Story {i}") for i in range(25))
    assert out[0].rstrip().endswith(f"(1/{len(out)})</i>") and out[-1].rstrip().endswith(f"({len(out)}/{len(out)})</i>")
    assert "(continued)" in out[1]                               # the section heading is repeated, marked
    for m in out:                                                # no card is cut in half
        assert m.count("<b>What happened:</b>") == m.count("🔗")


def test_a_single_oversized_block_is_split_without_breaking_tags():
    body = "<blockquote expandable><b>Notes</b>\n" + "\n".join(f"line {i} &amp; more" * 8 for i in range(300)) + \
           "</blockquote>"
    pieces = msg.split_html(body, 1500)
    assert len(pieces) > 5
    assert all(msg.check_html(p) == [] and msg.tg_len(p) <= 1500 for p in pieces)
    assert all(p.startswith("<blockquote expandable>") for p in pieces)


def test_emoji_are_counted_as_telegram_counts_them():
    assert msg.tg_len("🔥") == 2 and msg.tg_len("abc") == 3
    out = msg.paginate([("🔥" * 1500, None), ("🔥" * 1500, None)], limit=3900)
    assert len(out) == 2 and all(msg.tg_len(m) <= 4096 for m in out)


def test_clip_never_cuts_an_entity():
    text = msg.e(msg.clip("a & b & c & d " * 50, 40))
    assert msg.check_html(text) == [] and text.endswith("…")


# ----------------------------------------------------------------------------------------------- priorities

@pytest.mark.parametrize("story,expected", [
    ({"importance": 5, "status": "official", "section": "new_models"}, "P0"),
    ({"importance": 5, "status": "rumor", "section": "industry"}, "P1"),
    ({"importance": 4, "status": "official", "section": "model_updates"}, "P1"),
    ({"importance": 4, "status": "report", "section": "industry"}, "P2"),
    ({"importance": 3, "status": "report", "section": "research"}, "P2"),
    ({"importance": 4, "status": "official", "section": "free"}, "P3"),
    ({"importance": 2, "status": "report", "section": "industry"}, "P3"),
])
def test_digest_priorities(story, expected):
    assert digest.story_priority(story) == expected


def test_only_an_official_release_is_filed_as_a_new_model():
    items = [item("Grok 5 spotted", tier="leak"), item("Sam Altman on GPT-7", source="Pod")]
    items[1]["interview"] = True
    leak = {"items": [1], "status": "rumor", "company": "xai", "section": "new_models"}
    talk = {"items": [2], "status": "report", "company": "openai", "section": "industry", "interview": True}
    free = {"items": [1], "status": "official", "company": "free_models", "section": "industry"}
    assert digest.story_section(leak, items) == "industry"
    assert digest.story_section(talk, items) == "podcasts"
    assert digest.story_section(free, items) == "free"


def test_silent_delivery_follows_priority(monkeypatch):
    assert not msg.is_silent("P0") and not msg.is_silent("P1") and msg.is_silent("P2") and msg.is_silent("P3")
    assert not msg.is_silent("P2", {"telegram": {"silent_from": "P3"}})
    assert not msg.is_silent("P3", {"telegram": {"silent_from": "P4"}})
    sent = []

    class R:
        status_code, text = 200, ""

        def json(self):
            return {"ok": True, "result": {"message_id": 1}}
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(deliver.requests, "post", lambda url, data=None, timeout=None: sent.append(data) or R())
    deliver.send_telegram(["quiet"], silent=True)
    deliver.send_telegram(["loud"])
    assert sent[0]["disable_notification"] is True and "disable_notification" not in sent[1]


def test_flagship_release_is_p0_others_p1():
    google = RCFG["companies"]["google"]
    assert analyze.is_flagship(["gemini-5-pro"], [], [], google)
    assert not analyze.is_flagship(["gemini-5-flash-lite"], ["Gemini 5 Flash-Lite"],
                                   [{"text": "Gemini 5 Flash-Lite is fast and cheap."}], google)
    assert analyze.is_flagship(["gemini-5-neon"], ["Gemini 5 Neon"],
                               [{"text": "Gemini 5 Neon is our most capable model yet."}], google)
    assert analyze.update_priority("MAJOR_MODEL_UPGRADE", "OFFICIAL_UPDATE") == "P1"
    assert analyze.update_priority("MODEL_AVAILABILITY", "OFFICIAL_UPDATE") == "P2"


# ----------------------------------------------------------------------------------------------- digest

def _brief_items():
    items = [item("Anthropic releases Claude Opus 6", tier="official", company="anthropic",
                  url="https://www.anthropic.com/claude-opus-6", summary="Claude Opus 6 has a 2M context window."),
             item("OpenAI raises $10B", tier="press", company="openai", summary="Funding round of $10B."),
             item("New free model on OpenRouter: X", tier="leak", company="free_models")]
    answer = json.dumps({"headline": "Opus 6 is here", "stories": [
        {"company": "anthropic", "section": "new_models", "title": "Claude Opus 6 launches", "summary": "Released.",
         "why": "New flagship.", "details": ["2M context", "costs $99"], "status": "official", "importance": 5,
         "items": [1], "explain": {"introduced": "Claude Opus 6", "does": "Handles 2M tokens",
                                   "problem": "Saves 75% of cost"}},
        {"company": "openai", "section": "industry", "title": "OpenAI raises $10B", "summary": "A round.",
         "status": "report", "importance": 3, "items": [2], "explain": {"introduced": "money"}},
        {"company": "free_models", "section": "tools", "title": "X is free", "summary": "Free.", "status": "official",
         "importance": 3, "items": [3]}],
        "content": [{"kind": "news", "title": "Opus 6 explained", "angle": "What changed", "why": "Big",
                     "research": "Test 2M context"},
                    {"kind": "analysis", "title": "Opus 6 is 5x better?", "angle": "Check it"}]})
    return items, digest.parse_answer(answer, items, COMPANIES)


def test_digest_answer_gets_sections_priorities_and_checked_explanations():
    items, brief = _brief_items()
    opus, money, free = brief["stories"]
    assert (opus["section"], opus["priority"]) == ("new_models", "P0")
    assert (free["section"], free["priority"]) == ("free", "P3")
    assert opus["details"] == ["2M context"]                  # "$99" appears in no item: dropped
    assert opus["explain"] == {"introduced": "Claude Opus 6", "does": "Handles 2M tokens"}  # "75%" dropped
    assert money["explain"] == {}                             # explanations only for importance 4-5
    assert [o["title"] for o in brief["content"]] == ["Opus 6 explained"]  # "5x" appears in no item: dropped


def test_digest_message_uses_sections_and_is_valid_html():
    items, brief = _brief_items()
    meta = {"when": "now", "footer": "footer", "warning": "Not working: X", "covered": "Gemini 5 Pro"}
    out = digest.render_telegram(brief, items, COMPANIES, meta)
    text = "\n".join(out)
    assert_valid(out)
    for s in ("🛰 AI News Brief", "🚨 NEW MODEL RELEASES", "📰 AI INDUSTRY NEWS", "🆓 FREE AI MODELS AND TOOLS",
              "💡 CONTENT OPPORTUNITIES", "⚙️ AGENT HEALTH", "Not working: X", "already sent as alerts: Gemini 5 Pro",
              "🏢 Claude / Anthropic", "Why it matters:", "📘 Explained"):
        assert s in text, s
    assert text.index("🚨 NEW MODEL RELEASES") < text.index("📰 AI INDUSTRY NEWS") < text.index("🆓 FREE")


def test_digest_archive_stays_readable_by_the_weekly_report(isolated, monkeypatch):
    from agent import weekly

    items, brief = _brief_items()
    md = digest.render_markdown(brief, items, COMPANIES, {"when": "now", "footer": "f", "warning": None})
    (isolated / "digests").mkdir()
    (isolated / "digests" / f"{now_utc():%Y-%m-%d_%H%M}.md").write_text(md, encoding="utf-8")
    monkeypatch.setattr(weekly, "ROOT", isolated)
    stories = {s["title"]: s for s in weekly.read_week()}
    assert set(stories) == {"Claude Opus 6 launches", "OpenAI raises $10B", "X is free"}
    assert stories["Claude Opus 6 launches"]["priority"] == "P0" and stories["Claude Opus 6 launches"]["why"]
    out = weekly.render("The week", [(s, s["title"], s["summary"], s.get("why", "")) for s in stories.values()],
                        [{"kind": "news", "title": "Week video"}], "fake", 3)
    assert_valid(out)
    assert "🚨 NEW MODEL RELEASES" in "\n".join(out) and "A. NEWS: Week video" in "\n".join(out)


def test_digest_drops_stories_already_sent_as_release_alerts(isolated):
    items, brief = _brief_items()
    ledger = led.Ledger()
    ledger.record(["model:anthropic:claude-opus-6"], "Claude Opus 6", "release")
    covered = main.drop_covered_stories(brief, items, ledger)
    assert covered == ["Claude Opus 6"] and all("Opus 6" not in s["title"] for s in brief["stories"])
    _, brief2 = _brief_items()
    brief2["stories"][0]["update"] = True                     # a real new development is still reported
    assert main.drop_covered_stories(brief2, items, ledger) == []


# ----------------------------------------------------------------------------------------------- alerts & dedupe

SOURCES = {"Anthropic News": {"name": "Anthropic News", "type": "page_links", "tier": "official"},
           "Claude API pricing": {"name": "Claude API pricing", "type": "page_diff", "tier": "official", "tables": True},
           "TestingCatalog": {"name": "TestingCatalog", "type": "rss", "tier": "leak"},
           "OpenRouter free models": {"name": "OpenRouter free models", "type": "openrouter_models", "tier": "leak"}}


def _alert_item(title, source, tier, company, url, summary="", hours_ago=1):
    return item(title, source=source, tier=tier, company=company, url=url, summary=summary, hours_ago=hours_ago)


def test_alert_cards_filter_merge_and_hold(isolated):
    ledger = led.Ledger()
    ledger.record(["model:anthropic:claude-opus-6"], "Claude Opus 6", "release")
    items = [
        _alert_item("Claude API pricing: Footnotes", "Claude API pricing", "official", "anthropic",
                    "https://platform.claude.com/p", "Small wording fix in a footnote."),           # minor docs edit
        _alert_item("Claude API pricing: Opus", "Claude API pricing", "official", "anthropic",
                    "https://platform.claude.com/p2", "Opus input price changes to $3 per MTok"),    # significant
        _alert_item("Introducing Claude Opus 6", "Anthropic News", "official", "anthropic",
                    "https://www.anthropic.com/claude-opus-6"),                                     # already a release alert
        _alert_item("Grok 5 spotted in the Grok app", "TestingCatalog", "leak", None, "https://tc.com/grok5"),
        _alert_item("Grok 5 spotted in Grok app code", "TestingCatalog", "leak", None, "https://tc.com/grok5-b"),
        _alert_item("New free model on OpenRouter: Z", "OpenRouter free models", "leak", "free_models",
                    "https://openrouter.ai/z"),                                                     # P3: waits
    ]
    items[3]["companies"] = items[4]["companies"] = ["xai"]
    cards, kept, held = alerts.build_cards(items, SOURCES, COMPANIES, ledger, RCFG["companies"])
    reasons = {it["title"]: why for it, why in held}
    assert reasons["Claude API pricing: Footnotes"] == "minor documentation edit"
    assert reasons["Introducing Claude Opus 6"] == "already sent"
    assert reasons["New free model on OpenRouter: Z"].startswith("P3")
    assert [c["headline"] for c in cards] == ["Claude API pricing: Opus", "Grok 5 spotted in the Grok app"]
    grok = cards[1]
    assert len(grok["sources"]) == 2 and grok["details"] == ["reported by 2 sources"]   # one card, two links
    assert grok["verification"].startswith("👀") and grok["priority"] == "P2"


def test_alerts_run_sends_once_and_never_repeats(isolated, monkeypatch):
    src = [{"name": "TestingCatalog", "type": "rss", "tier": "leak", "alert": True}]
    feed = [_alert_item("Old xAI hiring news", "TestingCatalog", "leak", None, "https://tc.com/a", hours_ago=30)]
    feed[0]["companies"] = ["xai"]
    monkeypatch.setattr(main, "load", lambda: (CONFIG, src))
    monkeypatch.setattr(main, "answer_commands", lambda *a: None)
    monkeypatch.setattr(main, "refresh_bot_free_models", lambda *a, **k: None)
    monkeypatch.setattr(main, "fetch", lambda s, st: list(feed))
    monkeypatch.setattr(main, "telegram_configured", lambda: True)
    sent = []
    monkeypatch.setattr(main, "deliver", lambda config, msgs, email, channels, silent=False:
                        (sent.append((msgs, silent)) or ["telegram"], []))

    class Args:
        dry_run = False
    main.cmd_alerts(Args())                                    # first look at the source: learn quietly
    feed.append(_alert_item("Grok 5 spotted in xAI app testing", "TestingCatalog", "leak", None, "https://tc.com/b"))
    feed[-1]["companies"] = ["xai"]
    main.cmd_alerts(Args())
    assert len(sent) == 1
    messages, silent = sent[0]
    assert_valid(messages)
    assert "⚡ Instant alerts" in messages[0] and "📰 AI INDUSTRY NEWS" in messages[0] and silent is True
    # The same story again (new link, same or reworded headline): not sent again.
    for url, title in (("https://tc.com/c", "Grok 5 spotted in xAI app testing"),
                       ("https://tc.com/d", "Grok 5 spotted in testing")):
        feed.append(_alert_item(title, "TestingCatalog", "leak", None, url))
        feed[-1]["companies"] = ["xai"]
        main.cmd_alerts(Args())
    assert len(sent) == 1
    # A real development about the same model is still sent.
    feed.append(_alert_item("xAI releases Grok 5 to all users", "TestingCatalog", "leak", None, "https://tc.com/e"))
    feed[-1]["companies"] = ["xai"]
    main.cmd_alerts(Args())
    assert len(sent) == 2


def test_version_numbers_keep_stories_apart():
    assert led.similar("Grok 5 spotted in testing", "Grok 5 spotted in xAI app testing")
    assert not led.similar("Grok 5 spotted in testing", "Grok 6 spotted in testing")


def test_ledger_expires_old_entries(isolated, monkeypatch):
    ledger = led.Ledger()
    ledger.record(["url:a"], "A", "alerts")
    ledger.save()
    assert led.Ledger().sent(["url:a"])
    later = datetime.now(timezone.utc) + timedelta(days=led.KEEP_DAYS + 1)
    monkeypatch.setattr("agent.ledger.now_utc", lambda: later)
    assert led.Ledger().sent(["url:a"]) is None


def test_release_delivery_is_recorded_so_other_runs_skip_it(isolated, monkeypatch):
    from agent.releases import notify

    class R:
        status_code, text = 200, ""

        def json(self):
            return {"ok": True, "result": {"message_id": 9}}
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(deliver.requests, "post", lambda url, data=None, timeout=None: sent.append(data) or R())
    store = Store()
    rec = store.new_release("google", ["gemini-5-pro"], model_name="Gemini 5 Pro", verification="OFFICIAL_CONFIRMED",
                            official_sources=[{"source": "DeepMind", "url": "https://deepmind.google/x"}])
    store.enqueue(rec["id"], "update", "MODEL_AVAILABILITY", "OFFICIAL_UPDATE", "Gemini 5 Pro", ["hello"], "P2")
    notify.process_outbox(store, RCFG["settings"], store.save)
    assert sent[0].get("disable_notification") is True        # P2: silent
    ledger = led.Ledger()
    assert ledger.sent(["model:google:gemini-5-pro"])["channel"] == "release"
    assert ledger.sent([led.url_key("https://deepmind.google/x/")])


# ----------------------------------------------------------------------------------------------- producers

def test_every_producer_emits_valid_telegram_html(isolated):
    from agent import builders, buzz, health, weekly

    cand = {"kind": "repo", "id": "gh:a/b", "name": "a/<b>", "url": "https://github.com/a/b?x=1&y=2",
            "text": "A <tool> & more", "extra": "★ 5", "score": 1, "focus": True}
    outputs = [
        buzz.render("h & h", [(cand, "repo", "what <x>", "why & y", "talk")], "m", ["x"], 3, [],
                    [{"kind": "news", "title": "T"}]),
        builders.render("theme", [({**cand, "stars": 1}, "w", "who")], "m", []),
        weekly.render("h", [({"section": "🚨 NEW MODEL RELEASES", "label": "✅ Official · P0", "url": "https://x",
                              "date": "2026-11-12", "priority": "P0"}, "t", "s", "w")], [], "m", 1),
        health.render(runs={}),
        rrender.render_sources(RCFG, {"Kimi blog": 4}),
        rrender.render_recent([], RCFG["companies"]),
    ]
    for out in outputs:
        assert_valid(out)


def test_health_report_shows_runs_gaps_failures_and_delivery(isolated):
    from agent import health
    from agent.state import State

    s = State("alerts")
    s.failures = {"Claude Blog": 5}
    s.save()
    now = datetime.now(timezone.utc)
    runs = {"Instant alerts": [{"conclusion": "success", "created_at": (now - timedelta(hours=h)).isoformat(),
                                "html_url": "https://github.com/x"} for h in (1, 5, 12)],
            "AI news digest": [{"conclusion": "failure", "created_at": now.isoformat(), "html_url": "https://g/y"}]}
    text = "\n".join(health.render(runs))
    assert "✅" in text and "❌" in text and "actually ran every 4-7 h" in text
    assert "Claude Blog (alerts): failed 5 runs in a row" in text and "Release alert delivery" in text


# ----------------------------------------------------------------------------------------------- commands

def _command(monkeypatch, text):
    from agent import commands

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    class R:
        ok = True

        def json(self):
            return {"result": [{"update_id": 1, "message": {"chat": {"id": 42}, "text": text}}]}
    monkeypatch.setattr(commands.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(commands.requests, "post", lambda *a, **k: R())
    sent = []
    monkeypatch.setattr(commands, "send_telegram", lambda msgs: sent.extend(msgs))
    monkeypatch.setattr("agent.health.workflow_runs", lambda: None)
    commands.handle(CONFIG)
    return "\n".join(sent)


@pytest.fixture
def registry():
    store = Store()
    for company, mid, name, specs in [
            ("anthropic", "claude-opus-5-5", "Claude Opus 5.5", {"context_tokens": 1_000_000, "input_price": 4,
                                                                 "output_price": 20}),
            ("openai", "gpt-6.1-terra", "GPT-6.1 Terra", {"context_tokens": 1_050_000, "input_price": 1,
                                                          "output_price": 5})]:
        store.learn_model(company, {"id": mid, "name": name, "status": "active", "specs": specs}, "catalog")
    rec = store.new_release("google", ["gemini-5-pro"], model_name="Gemini 5 Pro", classification="NEW_MODEL_RELEASE",
                            verification="OFFICIAL_CONFIRMED", announcement_date="2026-11-12")
    rec["delivery"]["status"] = "SENT"
    store.new_release("xai", ["grok-5"], model_name="Grok 5", classification="UNCONFIRMED_EARLY_SIGNAL",
                      verification="UNCONFIRMED")
    store.save()
    return store


def test_compare_command(monkeypatch, registry):
    text = _command(monkeypatch, "/compare opus 5.5 vs GPT-6.1 Terra")
    assert "Claude Opus 5.5 vs GPT-6.1 Terra" in text and "$4 input / $20 output" in text
    assert "Input price drops from $4 to $1" in text and "Benchmarks and quality are not compared" in text
    assert "couldn't find <b>Gemini 99</b>" in _command(monkeypatch, "/compare opus 5.5 vs Gemini 99")
    assert "Name two models" in _command(monkeypatch, "/compare")


def test_models_command_lists_only_confirmed_releases(monkeypatch, registry):
    text = _command(monkeypatch, "/models")
    assert "Gemini 5 Pro" in text and "Grok 5" not in text
    assert "Grok 5" in _command(monkeypatch, "/releases")


def test_sources_health_weekly_and_help_commands(monkeypatch, isolated):
    from agent import commands

    assert "Monitored official sources" in _command(monkeypatch, "/sources")
    assert "AGENT HEALTH" in _command(monkeypatch, "/health")
    assert "No weekly report yet" in _command(monkeypatch, "/weekly")
    monkeypatch.setattr(commands, "LAST_WEEKLY", isolated / "w.json")
    (isolated / "w.json").write_text(json.dumps(["🗓 Weekly report"]))
    assert "🗓 Weekly report" in _command(monkeypatch, "/weekly")
    help_text = _command(monkeypatch, "/help")
    for c in ("/latest", "/buzz", "/repos", "/weekly", "/ask", "/models", "/releases", "/compare", "/free",
              "/sources", "/health"):
        assert c in help_text
