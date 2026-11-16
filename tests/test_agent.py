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
    assert sent[0] == "free" and sent[1].startswith("No brief has been sent") and "Your AI research assistant" in sent[2]
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
    own_site = item("Anthropic launches academy - Anthropic", source="Google News: Anthropic", tier="press",
                    url="https://www.anthropic.com/news/claude-frontier-academy")
    promoted = {"status": "report", "items": [1]}
    accuracy.enforce_status(promoted, [own_site])
    assert promoted["status"] == "official"  # published on the company's own site


def test_factcheck_fixes_and_drops(monkeypatch):
    from agent import factcheck as fc

    items = [item("Claude Opus 6 released", source="Anthropic News", tier="official", company="anthropic"),
             item("Unrelated GLM hacking benchmark", source="r/LocalLLaMA", tier="community")]
    brief = {"headline": "h", "post_ideas": [], "stories": [
        {"company": "anthropic", "title": "Opus 6 out with 10M context", "summary": "Claims 10M context.",
         "status": "official", "importance": 5, "items": [1, 2]},
        {"company": "zhipu", "title": "Made up", "summary": "x", "status": "rumor", "importance": 2, "items": [2]}]}
    answer = json.dumps({"headline": "Anthropic released Opus 6.", "checks": [
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
    assert out["stories"][0]["items"] == [1] and "2 corrected, 1 removed" in note
    assert out["headline"] == "Anthropic released Opus 6." and "Headline: h" in seen["text"]


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


def youtube_feed(videos):
    """A YouTube channel feed: videos = [(title, views, hours ago)]."""
    entries = "".join(
        f"""<entry><id>yt:video:v{n}</id><yt:videoId>v{n}</yt:videoId><title>{title}</title>
        <link rel="alternate" href="https://www.youtube.com/watch?v=v{n}"/><author><name>Channel</name></author>
        <published>{(now_utc() - timedelta(hours=hours)).isoformat()}</published>
        <media:group><media:description>Episode notes for {title}</media:description>
        <media:community><media:starRating count="1200" average="5" min="1" max="5"/>
        <media:statistics views="{views}"/></media:community></media:group></entry>"""
        for n, (title, views, hours) in enumerate(videos))
    return (f"""<?xml version="1.0" encoding="UTF-8"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
        xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom"><title>Channel</title>
        {entries}</feed>""").encode()


def test_youtube_source_marks_interviews(monkeypatch):
    from agent import sources

    feed = youtube_feed([("Sam Altman on GPT-6 | Ep. 42", 50_000, 5), ("OpenAI news roundup", 1_000, 3)])

    class R:
        content = feed
    monkeypatch.setattr(sources, "get", lambda url, **kw: R())
    plain = sources.fetch_youtube({"name": "News channel", "type": "youtube", "channel_id": "UCx"}, {})
    assert [i["interview"] for i in plain] == [True, False]  # "Ep. 42" in the title
    assert plain[0]["summary"].startswith("50,000 views on YouTube.") and plain[0]["id"] != plain[1]["id"]
    podcast = sources.fetch_youtube({"name": "Podcast", "type": "youtube", "channel_id": "UCx", "interview": True}, {})
    assert all(i["interview"] for i in podcast)
    assert sources.is_interview("AMA with the OpenAI Codex team") and not sources.is_interview("GPT-6 is out")


def reddit_feed(posts):
    """A Reddit RSS feed: posts = [(id, title, text)]."""
    entries = "".join(
        f"""<entry><category term="LocalLLaMA" label="r/LocalLLaMA"/><id>t3_{pid}</id><title>{title}</title>
        <link href="https://www.reddit.com/r/LocalLLaMA/comments/{pid}/"/><updated>2026-10-02T10:00:00+00:00</updated>
        <content type="html">&lt;p&gt;{text}&lt;/p&gt; submitted by /u/someone [link] [comments]</content></entry>"""
        for pid, title, text in posts)
    return f"""<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>
        {entries}</feed>""".encode()


def test_reddit_posts_and_interview_search(monkeypatch):
    from agent import buzz, sources

    class Feed:
        content = reddit_feed([("z", "Demis Hassabis interview on AGI timelines", "")])
    calls = []
    monkeypatch.setattr(sources, "get", lambda url, **kw: calls.append((url, kw.get("params"))) or Feed())
    posts = sources.reddit_posts(["singularity", "OpenAI"], "day", 50)
    assert calls[0] == ("https://www.reddit.com/r/singularity+OpenAI/top/.rss", {"t": "day", "limit": 50})
    assert posts[0]["sub"] == "LocalLLaMA" and posts[0]["text"] == "" and posts[0]["score"] is None
    sources.reddit_posts(["singularity"], "week", 25, "interview OR AMA")
    assert calls[1][0] == "https://www.reddit.com/r/singularity/search.rss" and calls[1][1]["q"] == "interview OR AMA"

    monkeypatch.setattr(buzz, "reddit_posts", lambda subs, period, limit, query=None: [] if query else posts)
    monkeypatch.setattr(buzz.time, "sleep", lambda s: None)
    cands = buzz.reddit({"buzz": {"reddit": {"subreddits": ["singularity"], "interview_search": "interview"}}})
    assert cands[0]["kind"] == "interview" and "#1 of today's top posts" in cands[0]["extra"]


def test_youtube_falls_back_to_the_videos_page(monkeypatch):
    from agent import sources

    data = {"contents": {"tabs": [{"richGridRenderer": {"contents": [
        {"richItemRenderer": {"content": {"videoRenderer": {   # older layout
            "videoId": "old1", "title": {"runs": [{"text": "Sam Altman interview: GPT-6"}]},
            "descriptionSnippet": {"runs": [{"text": "We talk about"}]},
            "viewCountText": {"simpleText": "1,234,567 views"}, "publishedTimeText": {"simpleText": "2 days ago"}}}}},
        {"richItemRenderer": {"content": {"lockupViewModel": {  # newer layout
            "contentId": "new1", "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "metadata": {"lockupMetadataViewModel": {
                "title": {"content": "Dario Amodei on Claude"},
                "metadata": {"contentMetadataViewModel": {"metadataRows": [{"metadataParts": [
                    {"text": {"content": "1.5M views"}}, {"text": {"content": "5 hours ago"}}]}]}}}}}}}},
    ]}}]}}
    page = f"<html><script>var ytInitialData = {json.dumps(data)};</script></html>"

    class R:
        def __init__(self, url):
            if "feeds/videos.xml" in url:
                raise ValueError("404 Client Error: Not Found")
            self.text = page
    monkeypatch.setattr(sources, "get", lambda url, **kw: R(url))
    videos = sources.youtube_videos("UCx")
    assert [(v["id"], v["title"], v["views"]) for v in videos] == [
        ("yt:video:old1", "Sam Altman interview: GPT-6", 1_234_567), ("yt:video:new1", "Dario Amodei on Claude", 1_500_000)]
    assert 47 <= (now_utc() - videos[0]["published"]).total_seconds() / 3600 <= 49
    assert videos[1]["url"] == "https://www.youtube.com/watch?v=new1" and videos[0]["description"] == "We talk about"
    items = sources.fetch_youtube({"name": "Podcast", "type": "youtube", "channel_id": "UCx", "interview": True}, {})
    assert items[0]["summary"].startswith("1,234,567 views on YouTube.") and all(i["interview"] for i in items)

    urls = []
    monkeypatch.setattr(sources, "get", lambda url, **kw: urls.append(url) or R(url))
    sources.youtube_videos("UCx", views=True)  # Buzz: the Videos page first, for real view counts
    assert urls == ["https://www.youtube.com/channel/UCx/videos"]

    combined = {"x": {"videoId": "c1", "title": {"simpleText": "My 2 views on AI"},
                      "viewCountText": {"simpleText": "Lex Fridman 12K views • 3 hours ago"}}}
    [v] = sources.parse_youtube_videos_page(f"<script>window[\"ytInitialData\"] = {json.dumps(combined)};</script>")
    assert v["views"] == 12_000 and 2.9 <= (now_utc() - v["published"]).total_seconds() / 3600 <= 3.1


def test_digest_marks_interviews(tmp_path, monkeypatch):
    from agent import weekly

    talk = item("Dario Amodei on Claude's next model", source="Dwarkesh Patel (YouTube)")
    talk["interview"] = True
    news = item("Anthropic raises prices", tier="official", company="anthropic")
    msg = digest.build_user_message([talk, news], now_utc())
    assert "(newsletter · interview · Dwarkesh Patel (YouTube)" in msg and "(official · Test Feed" in msg
    answer = json.dumps({"headline": "h", "stories": [
        {"company": "anthropic", "title": "Dario Amodei on the next Claude", "summary": "He said...", "status": "report",
         "importance": 4, "items": [1]},
        {"company": "anthropic", "title": "Prices go up", "status": "official", "importance": 3, "items": [2]}]})
    brief = digest.parse_answer(answer, [talk, news], COMPANIES)
    assert [s["interview"] for s in brief["stories"]] == [True, False]
    meta = {"when": "now", "footer": "f", "warning": None}
    md = digest.render_markdown(brief, [talk, news], COMPANIES, meta)
    assert "⭐ 🎙 **Dario Amodei on the next Claude**" in md
    assert "🎙" in "".join(digest.render_telegram(brief, [talk, news], COMPANIES, meta))

    (tmp_path / "digests").mkdir()
    (tmp_path / "digests" / f"{now_utc():%Y-%m-%d_%H%M}.md").write_text(md, encoding="utf-8")
    monkeypatch.setattr(weekly, "ROOT", tmp_path)
    stories = weekly.read_week()
    assert sorted((s["title"], s["interview"]) for s in stories) == [("Dario Amodei on the next Claude", True),
                                                                      ("Prices go up", False)]


def test_buzz_collect_pick_and_render(monkeypatch):
    from agent import builders, buzz

    trending_html = """<article class="Box-row"><h2><a href="/acme/agent-kit"> acme / agent-kit </a></h2>
      <p>Open-source toolkit for building AI agents</p><a href="/acme/agent-kit/stargazers">12,345</a>
      <span>1,234 stars today</span></article>
      <article class="Box-row"><h2><a href="/x/css-art">x / css-art</a></h2><p>Pretty CSS drawings</p>
      <span>900 stars today</span></article>"""

    class R:
        def __init__(self, data=None, text="", content=b""):
            self._data, self.text, self.content = data, text, content

        def json(self):
            return self._data

    def fake_get(url, params=None, **kw):
        params = params or {}
        if "trending" in url:
            return R(text=trending_html)
        if "search/repositories" in url:
            if "org:moonshotai" in params["q"].lower():
                return R({"items": [{"full_name": "MoonshotAI/Kimi-K3", "html_url": "https://github.com/MoonshotAI/Kimi-K3",
                                     "description": "Kimi K3 model code", "stargazers_count": 4000}]})
            return R({"items": []})
        if "api/models" in url:
            if params.get("search") == "uncensored":
                return R([{"id": "someone/Qwen4-uncensored", "likes": 80, "trendingScore": 12}])
            if params.get("author") == "MiniMaxAI":
                return R([{"id": "MiniMaxAI/MiniMax-M3", "likes": 300, "createdAt": now_utc().isoformat()}])
            if params.get("author"):
                return R([])
            return R([{"id": "zai-org/GLM-5.3", "likes": 500, "trendingScore": 40, "pipeline_tag": "text-generation"}])
        if "api/spaces" in url:
            return R([])
        if "daily_papers" in url:
            return R([{"paper": {"id": "2610.0001", "title": "Agents that sleep", "upvotes": 90}, "numComments": 4}])
        if "huggingface.co/blog" in url:
            return R(content=b"<rss><channel><item><title>Welcome GLM-5.3</title><link>https://huggingface.co/blog/glm</link>"
                             b"</item></channel></rss>")
        if "news.google.com" in url:
            return R(content=b"<rss><channel></channel></rss>")
        if "reddit.com" in url:
            if "search" in url:
                return R(content=reddit_feed([("ama", "AMA with the Qwen team", "")]))
            return R(content=reddit_feed([("x", "GLM-5.3 beats everything on my evals", "Long post"),
                                          ("y", "My cat", "")]))
        if "youtube.com/@" in url:
            return R(text='<link rel="alternate" href="https://www.youtube.com/feeds/videos.xml?channel_id=UC' + "a" * 22 + '">')
        if "feeds/videos.xml" in url:
            return R(content=youtube_feed([("Dario Amodei: Claude and the next five years", 600_000, 2),
                                           ("How we bake bread", 9_000, 3)]))
        raise AssertionError(url)
    monkeypatch.setattr(builders, "get", fake_get)
    monkeypatch.setattr(buzz.time, "sleep", lambda s: None)
    monkeypatch.setattr(buzz, "get", fake_get)
    monkeypatch.setattr("agent.sources.get", fake_get)

    config = {**CONFIG, "buzz": {**CONFIG["buzz"], "youtube": {"max_age_days": 4, "channels": [
        {"name": "Dwarkesh Patel", "handle": "DwarkeshPatel", "interview": True, "ai_only": True}]}}}
    ids = {}
    candidates, failed = buzz.collect(config, ids)
    by_name = {c["name"]: c for c in candidates}
    assert failed == []
    assert ids == {"DwarkeshPatel": "UC" + "a" * 22}  # remembered for the next run
    dario = by_name["Dario Amodei: Claude and the next five years"]
    assert dario["kind"] == "interview" and dario["focus"] and "600,000 views in 2h" in dario["extra"]
    assert "How we bake bread" not in by_name  # not about AI, from a channel marked ai_only
    glm = by_name["GLM-5.3 beats everything on my evals"]
    assert glm["kind"] == "reddit" and "r/LocalLLaMA · #1 of today's top posts" in glm["extra"] and glm["text"] == "Long post"
    assert by_name["AMA with the Qwen team"]["kind"] == "interview" and "top this week" in by_name["AMA with the Qwen team"]["extra"]
    assert {"acme/agent-kit", "x/css-art", "MoonshotAI/Kimi-K3", "MiniMaxAI/MiniMax-M3", "zai-org/GLM-5.3",
            "someone/Qwen4-uncensored", "Agents that sleep", "Welcome GLM-5.3"} <= set(by_name)  # non-AI repos kept too
    assert "not AI" in by_name["x/css-art"]["extra"]
    assert by_name["MoonshotAI/Kimi-K3"]["focus"] and by_name["someone/Qwen4-uncensored"]["uncensored"]
    assert len({c["kind"] for c in candidates[:4]}) == 4  # interleaved: each kind gets a turn near the top

    kimi = candidates.index(by_name["MoonshotAI/Kimi-K3"]) + 1
    answer = json.dumps({"headline": "Kimi K3 lands", "picks": [
        {"item": kimi, "type": "new model", "what": "Kimi's new model", "why": "4,000 stars", "talk": "Kimi is back"},
        {"item": 999, "type": "repo", "what": "made up"}, {"item": kimi, "type": "repo", "what": "duplicate"}]})
    monkeypatch.setattr(buzz.llm, "complete", lambda config, system, build: (answer, "gemini/x"))
    headline, picks, model, content = buzz.pick(CONFIG, candidates)
    assert headline == "Kimi K3 lands" and len(picks) == 1 and picks[0][1] == "new model"
    text = "\n".join(buzz.render(headline, picks, model, [], len(candidates)))
    assert "🔥 Buzz" in text and "🔄 MODEL UPDATES AND IMPROVEMENTS" in text and "Kimi is back" in text
    assert "https://github.com/MoonshotAI/Kimi-K3" in text and "🏢 MoonshotAI" in text
    more = buzz.more_interviews(candidates, picks)
    assert [c["name"] for c in more] == ["Dario Amodei: Claude and the next five years", "AMA with the Qwen team"]
    text = "\n".join(buzz.render(headline, picks, model, [], len(candidates), more))
    assert "🎙 AI PODCASTS AND INTERVIEWS" in text and "AMA with the Qwen team" in text and "Reddit and YouTube" in text

    def broken(*a, **k):
        raise RuntimeError("every AI is down")
    monkeypatch.setattr(buzz.llm, "complete", broken)
    headline, picks, model, content = buzz.pick(CONFIG, candidates)
    assert model == "none" and len(picks) == CONFIG["buzz"]["max_picks"] and content == []


def test_buzz_run_skips_items_already_sent(tmp_path, monkeypatch):
    from agent import buzz, commands

    monkeypatch.setattr("agent.state.ROOT", tmp_path)
    monkeypatch.setattr(commands, "LAST_BUZZ", tmp_path / "last_buzz.json")
    monkeypatch.setattr(main, "answer_commands", lambda *a: None)
    cands = [{"kind": "repo", "id": f"gh:a/{n}", "name": f"a/{n}", "url": f"https://github.com/a/{n}", "text": "AI tool",
              "extra": "", "score": n, "focus": False, "uncensored": False} for n in range(3)]
    monkeypatch.setattr(buzz, "collect", lambda config, ids=None: (list(cands), []))
    monkeypatch.setattr(buzz.llm, "complete", lambda *a: (json.dumps({"headline": "h", "picks": [{"item": 1, "type": "repo"}]}), "m"))
    sent = []
    monkeypatch.setattr(main, "telegram_configured", lambda: True)
    monkeypatch.setattr(main, "deliver", lambda config, msgs, email, channels, **kw: (sent.append(msgs) or ["telegram"], []))
    monkeypatch.setattr(main.botpush, "push", lambda *a, **k: True)

    class Args:
        dry_run = False
    assert main.cmd_buzz(Args()) == 0
    assert "a/0" in sent[0][0] and json.loads((tmp_path / "last_buzz.json").read_text()) == sent[0]
    assert main.cmd_buzz(Args()) == 0
    assert "a/0" not in sent[1][0] and "a/1" in sent[1][0]  # the first pick isn't repeated
