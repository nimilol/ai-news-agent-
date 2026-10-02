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
    monkeypatch.setattr(main.accuracy, "add_articles", lambda *a, **k: 0)
    monkeypatch.setattr(main.llm, "complete", lambda config, system, build: (build(), "fake/model") and (answer, "fake/model"))
    monkeypatch.setattr(main, "factcheck", lambda config, brief, items, writer: (brief, "fact-checked by fake"))

    class Args:
        dry_run = True
    assert main.cmd_digest(Args) == 0
    out = capsys.readouterr().out
    assert "GPT-6 launches" in out and "Grok 5 in testing" in out and "written by fake/model" in out
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
    text, used = llm.complete(config, "sys", lambda **kw: "user")
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


def test_free_model_sources(monkeypatch):
    from agent import sources

    openrouter = {"data": [
        {"id": "meta/llama-9:free", "name": "Llama 9 (free)", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "anthropic/claude-opus-6", "name": "Claude Opus 6", "pricing": {"prompt": "0.000004", "completion": "0.00002"}},
        {"id": "openrouter/mystery-alpha", "name": "Mystery Alpha", "pricing": {"prompt": "0", "completion": "0"}},
    ]}
    models_dev = {
        "opencode": {"name": "OpenCode Zen", "models": {
            "big-pickle": {"name": "Big Pickle", "cost": {"input": 0, "output": 0}, "limit": {"context": 200000}},
            "paid-model": {"name": "Paid", "cost": {"input": 3, "output": 15}}}},
        "anthropic": {"name": "Anthropic", "models": {"claude-opus-6": {"name": "Claude Opus 6", "release_date": "2026-10-01"}}},
    }

    class R:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data
    monkeypatch.setattr(sources, "get", lambda url, **kw: R(openrouter if "openrouter" in url else models_dev))

    free = sources.fetch_openrouter_models({"name": "OpenRouter free models", "mode": "free"}, {})
    assert {i["title"].split(": ", 1)[1] for i in free} == {"Llama 9 (free) (meta/llama-9:free)", "Mystery Alpha (openrouter/mystery-alpha)"}
    assert all(i["companies"] == ["free_models"] for i in free)

    default = sources.fetch_openrouter_models({"name": "OpenRouter new models"}, {})
    assert [(i["companies"], "stealth" in i["summary"]) for i in default] == [(["anthropic"], False), ([], True)]

    oc = sources.fetch_models_dev({"name": "OpenCode free models", "providers": {"opencode": "free_models"}, "free_only": True}, {})
    assert [i["title"] for i in oc] == ["New free model in OpenCode Zen: Big Pickle (big-pickle)"]
    early = sources.fetch_models_dev({"name": "Early model listings", "providers": {"anthropic": "anthropic", "zai": "zhipu"}}, {})
    assert [(i["title"], i["companies"]) for i in early] == [("New model listed for Anthropic: Claude Opus 6 (claude-opus-6)", ["anthropic"])]


def test_free_models_list(monkeypatch):
    from agent import freemodels

    now = now_utc().timestamp()
    openrouter = {"data": [
        {"id": "new/model:free", "name": "Brand New", "created": now - 86400, "context_length": 131072,
         "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "old/model:free", "name": "Old One", "created": now - 90 * 86400, "context_length": 1000000,
         "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "paid/model", "name": "Paid", "pricing": {"prompt": "0.001", "completion": "0.002"}},
    ]}
    models_dev = {"opencode": {"name": "OpenCode Zen", "models": {
        "big-pickle": {"name": "Big Pickle", "cost": {"input": 0, "output": 0}, "release_date": "2020-01-01"}}}}

    class R:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data
    monkeypatch.setattr(freemodels, "get", lambda url, **kw: R(openrouter if "openrouter" in url else models_dev))
    text = "\n".join(freemodels.render(freemodels.collect(CONFIG)))
    assert "<b>OpenRouter</b> (2 free)" in text and "<b>OpenCode Zen</b> (1 free)" in text
    assert text.index("🆕 <a href=\"https://openrouter.ai/new/model:free\">Brand New</a> · 131K context") < text.index("Old One")
    assert "Old One</a> · 1M context" in text and "Paid" not in text


def test_telegram_commands_only_answer_owner(tmp_path, monkeypatch):
    from agent import commands

    monkeypatch.setattr("agent.state.ROOT", tmp_path)
    monkeypatch.setattr(commands, "LAST_BRIEF", tmp_path / "last_brief.json")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    updates = [{"update_id": 7, "message": {"chat": {"id": 42}, "text": "/free"}},
               {"update_id": 8, "message": {"chat": {"id": 99}, "text": "/latest"}},   # a stranger
               {"update_id": 9, "message": {"chat": {"id": 42}, "text": "/latest@my_bot"}},
               {"update_id": 10, "message": {"chat": {"id": 42}, "text": "hello"}}]

    class R:
        ok = True

        def json(self):
            return {"result": updates}
    calls = []
    monkeypatch.setattr(commands.requests, "get", lambda url, params=None, timeout=None: calls.append(params) or R())
    monkeypatch.setattr(commands.requests, "post", lambda *a, **k: R())
    sent = []
    monkeypatch.setattr(commands, "send_free_models", lambda config: sent.append("free"))
    monkeypatch.setattr(commands, "send_telegram", lambda msgs: sent.append(msgs[0].split("\n")[0]))
    commands.handle(CONFIG)
    assert sent[0] == "free" and sent[1].startswith("No brief has been sent") and "Your AI news agent" in sent[2]
    assert len(sent) == 3  # the stranger's /latest was ignored
    updates.clear()
    commands.handle(CONFIG)  # next run continues after the last answered message
    assert calls[-1]["offset"] == 11


def test_accuracy_helpers():
    from agent import accuracy

    gn = item("OpenAI ships GPT-6 - The Verge", source="Google News: OpenAI", tier="press", hours_ago=3,
              url="https://news.google.com/rss/articles/x")
    tc = item("OpenAI GPT-6 spotted", source="TechCrunch AI", tier="press", hours_ago=5, url="https://techcrunch.com/a")
    tc2 = item("More on GPT-6", source="TechCrunch AI", tier="press", hours_ago=1, url="https://techcrunch.com/b")
    junk = item("GLM beats everyone - blockchain.news", source="Google News: Zhipu", tier="press")
    assert accuracy.publisher(gn) == "The Verge" and accuracy.publisher(tc) == "techcrunch.com"
    assert accuracy.is_blocked(junk, ["blockchain.news"]) and not accuracy.is_blocked(gn, ["blockchain.news"])
    items = [gn, tc, tc2]
    story = {"status": "official", "items": [1, 2, 3], "title": "t", "summary": "s"}
    assert accuracy.source_count(story, items) == 2  # two TechCrunch links count once
    accuracy.enforce_status(story, items)
    assert story["status"] == "report"  # no official source, so it can't be "official"
    line = accuracy.badges(story, items)
    assert "2 sources" in line and "🕐" in line
    assert "single source" in accuracy.badges({"status": "rumor", "items": [2]}, items)


def test_factcheck_fixes_and_drops(monkeypatch):
    from agent import factcheck as fc

    items = [item("Claude Opus 6 released", source="Anthropic News", tier="official", company="anthropic"),
             item("Unrelated GLM hacking benchmark", source="r/LocalLLaMA", tier="community")]
    brief = {"headline": "h", "post_ideas": [], "stories": [
        {"company": "anthropic", "title": "Opus 6 out with 10M context", "summary": "Claims 10M context.",
         "status": "official", "importance": 5, "items": [1, 2]},
        {"company": "zhipu", "title": "Made up", "summary": "x", "status": "rumor", "importance": 2, "items": [2]}]}
    answer = json.dumps({"checks": [
        {"story": 1, "verdict": "fix", "title": "Claude Opus 6 released", "summary": "Anthropic released Opus 6.",
         "remove_items": [2]},
        {"story": 2, "verdict": "drop"}]})
    seen = {}

    def fake_complete(config, system, build):
        seen["order"] = config["llm"]["order"]
        seen["text"] = build(summary_chars=100)
        return answer, "groq/x"
    monkeypatch.setattr(fc.llm, "complete", fake_complete)
    out, note = fc.factcheck(CONFIG, brief, items, "gemini/gemini-flash-latest")
    assert seen["order"][0] != "gemini" and seen["order"][-1] == "gemini"  # a different model checks the work
    assert [s["title"] for s in out["stories"]] == ["Claude Opus 6 released"]
    assert out["stories"][0]["items"] == [1] and "1 corrected, 1 removed" in note


def test_builder_picks(monkeypatch):
    from agent import builders

    trending_html = """<article class="Box-row"><h2><a href="/acme/agent-kit"> acme / agent-kit </a></h2>
      <p>Open-source toolkit for building AI agents with MCP</p><span itemprop="programmingLanguage">Python</span>
      <a href="/acme/agent-kit/stargazers">12,345</a><span>1,234 stars today</span></article>
      <article class="Box-row"><h2><a href="/x/css-art">x / css-art</a></h2><p>Pretty CSS drawings</p></article>"""

    class R:
        def __init__(self, data=None, text=""):
            self._data, self.text = data, text

        def json(self):
            return self._data

    def fake_get(url, params=None, **kw):
        if "trending" in url:
            return R(text=trending_html)
        if "search/repositories" in url:
            return R({"items": [{"full_name": "new/rag-lab", "html_url": "https://github.com/new/rag-lab",
                                 "description": "RAG evals", "stargazers_count": 900, "language": "TypeScript"}]})
        if "api/models" in url:
            return R([{"id": "zai-org/GLM-5.3", "likes": 500, "pipeline_tag": "text-generation"}])
        if "api/spaces" in url:
            return R([])
        return R({"hits": [{"objectID": "1", "title": "Show HN: A voice agent in 200 lines", "points": 120, "url": "https://v.dev"}]})
    monkeypatch.setattr(builders, "get", fake_get)
    candidates, failed = builders.collect(CONFIG)
    names = {c["name"] for c in candidates}
    assert failed == [] and "acme/agent-kit" in names and "x/css-art" not in names  # non-AI repos filtered out
    assert {"new/rag-lab", "zai-org/GLM-5.3", "A voice agent in 200 lines"} <= names
    trend = next(c for c in candidates if c["name"] == "acme/agent-kit")
    assert trend["stars"] == 12345 and "+1,234 stars today" in trend["extra"]

    answer = json.dumps({"theme": "Agents everywhere", "picks": [
        {"item": 1, "what": "Toolkit for agents", "who": "Python devs"}, {"item": 99, "what": "bogus"}]})
    monkeypatch.setattr(builders.llm, "complete", lambda config, system, build: (answer, "gemini/x"))
    theme, picks, model = builders.pick(CONFIG, candidates)
    assert theme == "Agents everywhere" and len(picks) == 1 and picks[0][1] == "Toolkit for agents"
    text = "\n".join(builders.render(theme, picks, model, []))
    assert "🛠 Builder picks" in text and "For: Python devs" in text


def test_ask_and_repos_commands(tmp_path, monkeypatch):
    from agent import commands

    monkeypatch.setattr("agent.state.ROOT", tmp_path)
    monkeypatch.setattr(commands, "ROOT", tmp_path)
    monkeypatch.setattr(commands, "LAST_REPOS", tmp_path / "last_repos.json")
    (tmp_path / "digests").mkdir()
    (tmp_path / "digests" / f"{now_utc():%Y-%m-%d_%H%M}.md").write_text("# Brief\nOpenAI launched GPT-6.")
    commands.save_json(commands.LAST_REPOS, ["🛠 picks"])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    updates = [{"update_id": 1, "message": {"chat": {"id": 42}, "text": "/repos"}},
               {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/ask What did OpenAI launch?"}}]

    class R:
        ok = True

        def json(self):
            return {"result": updates}
    monkeypatch.setattr(commands.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(commands.requests, "post", lambda *a, **k: R())
    asked = {}

    def fake_complete(config, system, build):
        asked["user"] = build()
        return "OpenAI launched GPT-6.", "gemini/x"
    monkeypatch.setattr("agent.llm.complete", fake_complete)
    sent = []
    monkeypatch.setattr(commands, "send_telegram", lambda msgs: sent.extend(msgs))
    commands.handle(CONFIG)
    assert sent[0] == "🛠 picks"
    assert "What did OpenAI launch?" in sent[1] and "GPT-6" in sent[1] and "GPT-6" in asked["user"]
