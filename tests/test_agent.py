"""Offline tests: run with `python -m pytest -q`. No network or API keys needed."""

import json
from datetime import timedelta

import pytest

import agent.__main__ as main
from agent import digest
from agent.common import load_yaml, make_item, now_utc
from agent.state import State

CONFIG = load_yaml("config.yaml")
COMPANIES = CONFIG["companies"]


def item(title, source="Test Feed", tier="newsletter", company=None, hours_ago=1, url=None, summary=""):
    src = {"name": source, "tier": tier, **({"company": company} if company else {})}
    published = now_utc() - timedelta(hours=hours_ago) if hours_ago is not None else None
    return make_item(src, title, url or f"https://example.com/{abs(hash(title))}", summary, published)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.state.ROOT", tmp_path)
    return State("test")


@pytest.mark.parametrize("title,expected", [
    ("Anthropic releases Claude Opus 6", ["anthropic"]),
    ("OpenAI ships GPT-6 to ChatGPT Plus users", ["openai"]),
    ("xAI's Grok 5 tops the leaderboard", ["xai"]),
    ("Zhipu open-sources GLM-5 on Hugging Face", ["zhipu"]),
    ("Groq raises money for faster chips", []),           # Groq is not Grok
    ("New Gemini model from Google", []),
])
def test_company_tagging(title, expected):
    it = item(title)
    main.tag_companies(it, main.company_matchers(CONFIG))
    assert it["companies"] == expected


def test_first_run_is_quiet_then_reports_new_items(state, monkeypatch):
    src = {"name": "OpenAI News", "type": "rss", "company": "openai", "tier": "official"}
    old = item("Older OpenAI post", source="OpenAI News", company="openai", hours_ago=30)
    recent = item("Brand new OpenAI post", source="OpenAI News", company="openai", hours_ago=2)
    undated = item("Undated page link", source="OpenAI News", company="openai", hours_ago=None)
    monkeypatch.setattr(main, "fetch", lambda s, st: [old, recent, undated])
    found, failed, _ = main.collect([src], state, CONFIG)
    assert [i["title"] for i in found] == ["Brand new OpenAI post"]  # only the last 12h on first run

    fresh = item("Next day OpenAI post", source="OpenAI News", company="openai", hours_ago=0, url="https://x/new")
    undated2 = item("Another page link", source="OpenAI News", company="openai", hours_ago=None, url="https://x/u2")
    monkeypatch.setattr(main, "fetch", lambda s, st: [fresh, undated2, old, recent, undated])
    found, _, _ = main.collect([src], state, CONFIG)
    assert sorted(i["title"] for i in found) == ["Another page link", "Next day OpenAI post"]


def test_unrelated_items_from_general_sources_are_dropped(state, monkeypatch):
    src = {"name": "Newsletter", "type": "rss", "tier": "newsletter"}
    monkeypatch.setattr(main, "fetch", lambda s, st: [
        item("Claude gets memory", source="Newsletter"), item("Apple event recap", source="Newsletter")])
    state.mark_initialized("Newsletter")
    found, _, _ = main.collect([src], state, CONFIG)
    assert [i["title"] for i in found] == ["Claude gets memory"]


def test_broken_source_is_reported_not_fatal(state, monkeypatch):
    def boom(s, st):
        raise ValueError("HTTP 403")
    monkeypatch.setattr(main, "fetch", boom)
    for _ in range(3):
        found, failed, _ = main.collect([{"name": "Broken", "type": "rss"}], state, CONFIG)
    assert failed == ["Broken"] and found == []
    assert "Broken" in main.failing_warning(state)


def test_parse_answer_keeps_only_grounded_stories():
    items = [item("Claude Opus 6 launched", tier="official", company="anthropic"), item("GPT-6 rumor")]
    answer = "```json\n" + json.dumps({
        "headline": "Anthropic ships Opus 6",
        "stories": [
            {"company": "anthropic", "title": "Opus 6 is out", "summary": "Big.", "status": "official",
             "importance": 5, "items": [1]},
            {"company": "openai", "title": "Made-up story", "summary": "x", "status": "rumor",
             "importance": 4, "items": [99]},  # points at an item that doesn't exist -> dropped
            {"company": "openai", "title": "GPT-6 whispers", "summary": "y", "status": "weird",
             "importance": "9", "items": ["2"]},
        ],
        "post_ideas": ["Opus 6 in 60 seconds"],
    }) + "\n```"
    brief = digest.parse_answer(answer, items, COMPANIES)
    assert [s["title"] for s in brief["stories"]] == ["Opus 6 is out", "GPT-6 whispers"]
    assert brief["stories"][1]["importance"] == 5 and brief["stories"][1]["status"] == "report"


def test_parse_answer_rejects_garbage():
    with pytest.raises(ValueError):
        digest.parse_answer("Sorry, I can't help with that.", [item("Claude news")], COMPANIES)


def test_telegram_render_escapes_and_splits():
    items = [item(f"Claude &lt;update&gt; #{n} & more", company="anthropic", url=f"https://e.com/{n}?a=1&b=2")
             for n in range(60)]
    brief = digest.fallback_brief(items, COMPANIES, per_company=60)
    for s in brief["stories"]:
        s["summary"] = "A fairly long summary sentence about what changed. " * 3
    meta = {"when": "now", "footer": "footer", "warning": None}
    messages = digest.render_telegram(brief, items, COMPANIES, meta)
    assert len(messages) > 1
    assert all(len(m) <= 4096 for m in messages)
    assert "&lt;update&gt;" in messages[0] and "a=1&amp;b=2" in messages[0]


def test_dry_run_digest_end_to_end(tmp_path, monkeypatch, capsys):
    """Full digest with a fake AI provider and fake sources: nothing is sent or saved."""
    monkeypatch.setattr("agent.state.ROOT", tmp_path)
    items = [item("OpenAI launches GPT-6", source="OpenAI News", tier="official", company="openai", url="https://openai.com/a"),
             item("Grok 5 spotted in testing", source="TestingCatalog", tier="leak", url="https://tc.com/b")]
    monkeypatch.setattr(main, "load", lambda: (CONFIG, [
        {"name": "OpenAI News", "type": "rss", "company": "openai", "tier": "official"},
        {"name": "TestingCatalog", "type": "rss", "tier": "leak"}]))
    monkeypatch.setattr(main, "fetch", lambda s, st: [i for i in items if i["source"] == s["name"]])
    answer = json.dumps({"headline": "GPT-6 is here", "stories": [
        {"company": "openai", "title": "GPT-6 launches", "summary": "OpenAI released GPT-6.", "status": "official",
         "importance": 5, "items": [1]},
        {"company": "xai", "title": "Grok 5 in testing", "summary": "Spotted in the app.", "status": "rumor",
         "importance": 4, "items": [2]}], "post_ideas": ["GPT-6: what's new"]})
    monkeypatch.setattr(main.llm, "complete", lambda config, system, build: (build(), "fake/model") and (answer, "fake/model"))

    class Args:
        dry_run = True
    assert main.cmd_digest(Args) == 0
    out = capsys.readouterr().out
    assert "GPT-6 launches" in out and "Grok 5 in testing" in out and "AI: fake/model" in out
    assert not (tmp_path / "state" / "digest.json").exists()  # dry run saves nothing


def test_provider_chain_falls_through(monkeypatch):
    """First model is retired (404), the second rejects JSON mode (400) then answers without it."""
    from agent import llm

    calls = []

    class Resp:
        def __init__(self, code, body):
            self.status_code, self._body, self.text = code, body, json.dumps(body)

        def json(self):
            return self._body

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((json["model"], "response_format" in json))
        if json["model"] == "old-model":
            return Resp(404, {"error": "model not found"})
        if "response_format" in json:
            return Resp(400, {"error": "response_format unsupported"})
        return Resp(200, {"choices": [{"message": {"content": '{"stories": []}'}}]})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    for name in ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "ZAI_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    config = {"llm": {"order": ["anthropic", "gemini"], "providers": {
        "anthropic": {"key_env": "ANTHROPIC_API_KEY", "models": ["claude-opus-5-5"]},
        "gemini": {"key_env": "GEMINI_API_KEY", "base_url": "https://x", "models": ["old-model", "new-model"]}}}}
    text, used = llm.complete(config, "sys", lambda n=None, c=None: "user")
    assert used == "gemini/new-model" and text == '{"stories": []}'
    assert calls == [("old-model", True), ("new-model", True), ("new-model", False)]


def test_reddit_combined_feed_tags_company_by_subreddit(monkeypatch):
    from agent import sources

    atom = b"""<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>top</title>
    <entry><category term="ClaudeAI" label="r/ClaudeAI"/><id>t3_a</id><title>Opus feels faster today</title>
      <link href="https://www.reddit.com/r/ClaudeAI/comments/a/"/><updated>2026-10-02T10:00:00+00:00</updated>
      <content type="html">&lt;p&gt;Anyone else?&lt;/p&gt;</content></entry>
    <entry><category term="LocalLLaMA" label="r/LocalLLaMA"/><id>t3_b</id><title>GLM-5 weights are out</title>
      <link href="https://www.reddit.com/r/LocalLLaMA/comments/b/"/><updated>2026-10-02T11:00:00+00:00</updated></entry>
    </feed>"""

    class R:
        content = atom
    urls = []
    monkeypatch.setattr(sources, "get", lambda url, **kw: urls.append(url) or R())
    src = {"name": "Reddit", "type": "reddit", "tier": "community",
           "subreddits": {"ClaudeAI": "anthropic", "LocalLLaMA": None}}
    items = sources.fetch_reddit(src, {})
    assert urls == ["https://www.reddit.com/r/ClaudeAI+LocalLLaMA/top/.rss?t=day&limit=100"]
    assert [(i["source"], i["companies"]) for i in items] == [("r/ClaudeAI", ["anthropic"]), ("r/LocalLLaMA", [])]
    assert items[0]["summary"] == "Anyone else?" and items[0]["published"].startswith("2026-10-02T10:00")


def test_exclude_skips_prereleases(state, monkeypatch):
    src = {"name": "Codex releases", "type": "rss", "company": "openai", "tier": "official", "exclude": "alpha|beta|-rc"}
    state.mark_initialized("Codex releases")
    monkeypatch.setattr(main, "fetch", lambda s, st: [
        item("0.162.0-alpha.7", source="Codex releases", company="openai"),
        item("0.162.0", source="Codex releases", company="openai")])
    found, _, _ = main.collect([src], state, CONFIG)
    assert [i["title"] for i in found] == ["0.162.0"]


def test_busy_provider_is_retried(monkeypatch):
    from agent import llm

    codes = iter([503, 503, 200])

    class Resp:
        def __init__(self, code):
            self.status_code = code
            self.text = "busy" if code != 200 else ""
            self.url, self.headers = "https://x/chat/completions", {}

        def json(self):
            return {"choices": [{"message": {"content": '{"stories": []}'}}]}

    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: Resp(next(codes)))
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    assert llm._chat_completions("https://x", "k", "m", "sys", "user") == '{"stories": []}'
