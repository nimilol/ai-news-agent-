"""Offline tests for Model Access Intelligence (agent/modelaccess/). Every catalog, page and the Telegram Bot API are
faked; nothing touches the network and no real message is sent."""

import json

import pytest

from agent import deliver, freemodels, messages as msg
from agent.modelaccess import alerts as al, analyze, config as mconfig, normalize as nz, pipeline, respond
from agent.modelaccess import sources as src
from agent.modelaccess.db import AccessDB

CFG = mconfig.load()
S = CFG["settings"]
PREFIX = "[MODEL ACCESS INTELLIGENCE]"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.state.ROOT", tmp_path)
    monkeypatch.setattr("agent.botpush.push", lambda *a, **k: True)
    monkeypatch.setattr("agent.botpush.bot_url", lambda: None)
    monkeypatch.setattr("agent.modelaccess.sources.RETRY_WAITS", (0, 0))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    def no_network(*a, **k):
        raise deliver.TelegramError("network disabled in tests", None, False)
    monkeypatch.setattr("agent.releases.notify.send_telegram_message", no_network)
    monkeypatch.setattr("agent.releases.notify.edit_telegram_message", lambda *a, **k: None)
    return tmp_path


def listing(name, cin, cout, canon=None, cache=None, ctx=200000, tools=True, reasoning=True, ow=False, **kw):
    m = {"id": kw.pop("lid", name), "name": name, "reasoning": reasoning, "tool_call": tools, "open_weights": ow,
         "release_date": kw.pop("release", "2026-10-01"), "limit": {"context": ctx, "output": 32000},
         "modalities": {"input": ["text"], "output": ["text"]}}
    if cin is not None:
        m["cost"] = {"input": cin, "output": cout, **({"cache_read": cache} if cache is not None else {})}
    if canon:
        m["canonical_model_id"] = canon
    m.update(kw)
    return m


def models_dev():
    return {
        "anthropic": {"id": "anthropic", "name": "Anthropic", "doc": "https://docs.anthropic.com/models", "models": {
            "claude-fable-5-2": listing("Claude Fable 5.2", 8, 40, "anthropic/claude-fable-5-2", cache=0.2, ctx=1000000)}},
        "togetherai": {"id": "togetherai", "name": "Together AI", "doc": "https://together.ai", "models": {
            "fable": listing("Claude Fable 5.2", 6, 30, "anthropic/claude-fable-5-2", cache=0.15, ctx=1000000),
            "llama": listing("Llama 5 70B", 0.6, 0.6, "meta-llama/llama-5-70b", ow=True, ctx=128000)}},
        "cheapresell": {"id": "cheapresell", "name": "CheapResell", "doc": "https://cheap.example", "models": {
            "fable": listing("Claude Fable 5.2", 1, 5, "anthropic/claude-fable-5-2", ctx=1000000)}},
        "deepseek": {"id": "deepseek", "name": "DeepSeek", "doc": "https://api-docs.deepseek.com", "models": {
            "deepseek-v4-flash": listing("DeepSeek V4 Flash", 0.1, 0.3, "deepseek/deepseek-v4-flash", cache=0.01,
                                         ow=True, ctx=1000000)}},
        "mysterycloud": {"id": "mysterycloud", "name": "MysteryCloud", "doc": "https://mystery.example", "models": {
            "dsv4": listing("DeepSeek V4 Flash", 0, 0, "deepseek/deepseek-v4-flash")}},
        "zai-coding-plan": {"id": "zai-coding-plan", "name": "Z.AI Coding Plan", "doc": "https://z.ai", "models": {
            "fable": listing("Claude Fable 5.2", 0, 0, "anthropic/claude-fable-5-2")}},
        "google": {"id": "google", "name": "Google", "doc": "https://ai.google.dev", "models": {
            "gemini-4-argon": listing("Gemini 4 Argon", 2, 12, "google/gemini-4-argon", ctx=1000000),
            "gemini-3-8-flash": listing("Gemini 3.8 Flash", 0.75, 3.75, None, lid="gemini-3.8-flash", ctx=1000000),
            "lyria-3-pro-preview": listing("Lyria 3 Pro", 0, 0, "google/lyria-3-pro-preview", tools=False),
            "gemma-4-31b-it": listing("Gemma 4 31B", 0, 0, None, ow=True, ctx=128000)}},
        "abliteratedllm": {"id": "abliteratedllm", "name": "Abliterated", "doc": "x", "models": {
            "x": listing("Some model", 0, 0, "deepseek/deepseek-v4-flash")}},
        "huggingface": {"id": "huggingface", "name": "Hugging Face", "doc": "https://hf.co", "models": {
            "unc": listing("DeepSeek-V4-Pro-Qwen3.5-4B-UNCENSORED-heretic", 0.01, 0.01, "community/ds-v4-unc")}},
    }


def openrouter_rows():
    def row(rid, name, pin, pout, ctx=200000, params=("tools", "reasoning")):
        return {"id": rid, "name": name, "context_length": ctx, "pricing": {"prompt": str(pin), "completion": str(pout)},
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "supported_parameters": list(params), "top_provider": {"max_completion_tokens": 8000}}
    return [row("deepseek/deepseek-v4-flash:free", "DeepSeek: DeepSeek V4 Flash (free)", 0, 0),
            row("deepseek/deepseek-v4-flash", "DeepSeek: DeepSeek V4 Flash", 0.0000001, 0.0000003),
            row("google/lyria-3-pro-preview", "Google: Lyria 3 Pro", 0, 0),
            row("openrouter/auto", "Auto", -1, -1),
            row("anthropic/claude-fable-5.2", "Anthropic: Claude Fable 5.2", 0.000008, 0.00004, 1000000)]


GEMINI_MD = """# Pricing

## Gemini 4 Argon  {% id="gemini-4-argon" %}
  *[`gemini-4-argon`](/docs/models/gemini-4-argon)*
{% callout type="note" %}
  Gemini 4 Argon is not available on the Free tier.
{% /callout %}
  * Input price
  * Not available
  * $2.00

## Gemini 3.8 Flash  {% id="gemini-3.8-flash" %}
  *[`gemini-3.8-flash`](/docs/models/gemini-3.8-flash)*
  * Input price
  * Free of charge
  * $0.75
  * Used to improve our products
  * [Yes](/docs/terms)
  * [No](/docs/terms)

## Lyria 3  {% id="lyria-3" %}
  *[`lyria-3-pro-preview`](/docs/models/lyria-3)*
  * Input price
  * Not available
  * $0.04 per song
"""


def build(verified=None, md=None, orr=None, gem=True):
    gemini = src.parse_gemini_pricing(GEMINI_MD) if gem else None
    b = nz.Builder(CFG, {"openrouter": True} if verified is None else verified)
    b.add_models_dev(md if md is not None else models_dev(), gemini)
    b.add_openrouter(orr if orr is not None else openrouter_rows())
    if gemini:
        b.add_gemini_free(gemini, "https://ai.google.dev/gemini-api/docs/pricing")
    return b.finish()


def fresh_db(**kw):
    models, offers = build(**kw)
    db = AccessDB()
    db.apply(models, offers, {"models_dev", "openrouter", "gemini"})
    return db


# ---------------------------------------------------------------------------------------------- discovery & status

def test_models_are_discovered_from_catalogs_not_a_hardcoded_list():
    models, offers = build()
    assert "anthropic/claude-fable-5-2" in models          # Anthropic listing + Together + OpenRouter = one model
    assert "meta/llama-5-70b" in models                    # "meta-llama" alias grouped under Meta
    assert "deepseek/deepseek-v4-flash" in models
    fable = [o for o in offers.values() if o["model"] == "anthropic/claude-fable-5-2"]
    assert {o["provider"] for o in fable} >= {"anthropic", "togetherai", "openrouter", "cheapresell", "zai-coding-plan"}
    assert models["anthropic/claude-fable-5-2"]["name"] == "Claude Fable 5.2"  # the official listing's name wins


def test_zero_prices_are_never_taken_as_free_without_confirmation():
    _, offers = build()
    assert offers["mysterycloud:deepseek/deepseek-v4-flash:free"]["status"] == "UNVERIFIED"    # unknown provider
    sub = offers["zai-coding-plan:anthropic/claude-fable-5-2"]
    assert sub["access"] == "subscription" and sub["status"] == "PAID_ONLY"                   # "included", not free
    lyria = offers["google:google/lyria-3-pro-preview:free"]
    assert lyria["status"] == "UNVERIFIED" and "pricing page" in lyria["note"]               # contradicted officially
    assert offers["openrouter:google/lyria-3-pro-preview:free"]["status"] == "UNVERIFIED"    # $0 without :free
    assert offers["google:google/gemma-4-31b-it:free"]["status"] == "LIMITED_FREE"           # open model, free tier


def test_free_tiers_are_limited_and_carry_their_terms():
    _, offers = build()
    o = offers["openrouter:deepseek/deepseek-v4-flash:free"]
    assert o["status"] == "LIMITED_FREE" and o["verification"] == "VERIFIED"
    assert "50 requests/day" in o["terms"]["daily_limit"] and "20 requests/minute" == o["terms"]["rate_limit"]
    g = offers["google:google/gemini-3-8-flash:free"]
    assert g["verification"] == "OFFICIAL" and "improve" in g["terms"]["notes"]
    assert "google:google/gemini-4-argon:free" not in offers                                   # no Free tier
    _, offers = build(verified={})
    assert offers["openrouter:deepseek/deepseek-v4-flash:free"]["verification"] == "CATALOG"  # terms not re-checked


def test_excluded_models_blocked_providers_and_router_pseudo_models():
    models, offers = build()
    assert not any("uncensored" in m["name"].lower() or "heretic" in m["name"].lower() for m in models.values())
    assert not any(o["provider"] == "abliteratedllm" for o in offers.values())
    assert not any(o.get("listing") == "openrouter/auto" for o in offers.values())


def test_model_status_is_its_best_confirmed_offer():
    db = fresh_db()
    assert db.models["deepseek/deepseek-v4-flash"]["status"] == "FREE"            # open weights (self-hosted)
    assert db.models["google/gemini-3-8-flash"]["status"] == "LIMITED_FREE"
    assert db.models["anthropic/claude-fable-5-2"]["status"] == "PAID_ONLY"
    assert nz.model_status([{"status": "UNVERIFIED", "verification": "UNVERIFIED"}]) == "UNVERIFIED"
    assert nz.model_status([{"status": "LOW_COST", "verification": "CATALOG"},
                            {"status": "LIMITED_FREE", "verification": "UNVERIFIED"}]) == "LOW_COST"


def test_low_cost_threshold_and_blended_price():
    assert nz.blended({"in": 1, "out": 5}) == 2.0
    _, offers = build()
    assert offers["deepseek:deepseek/deepseek-v4-flash"]["status"] == "LOW_COST"
    assert offers["anthropic:anthropic/claude-fable-5-2"]["status"] == "PAID_ONLY"


# ---------------------------------------------------------------------------------------------- provider comparison

def test_picks_ignore_unvetted_resellers_and_prefer_official_on_ties():
    db = fresh_db()
    v = analyze.model_view(db, "anthropic/claude-fable-5-2", S)
    assert v["picks"]["cheapest"]["provider"] == "togetherai"        # the $1/$5 reseller is never picked
    assert v["picks"]["official"]["provider"] == "anthropic"
    assert v["picks"]["best_free"] is None
    ds = analyze.model_view(db, "deepseek/deepseek-v4-flash", S)
    assert ds["picks"]["best_free"]["provider"] == "openrouter"        # hosted free beats self-hosting
    assert ds["picks"]["cheapest"]["provider"] in ("deepseek", "openrouter")
    assert ds["picks"]["cheapest"]["blended"] == pytest.approx(0.15)
    assert ds["picks"]["agents"]["price"]["cache"] is not None or ds["picks"]["agents"]["provider"] == "openrouter"


def test_compare_lists_resellers_with_a_label_and_subscriptions_separately():
    db = fresh_db()
    bundle, _, _ = respond.build(db, CFG)
    text = "\n".join(respond.route(bundle, "compare claude fable 5.2"))
    assert "CheapResell" in text and "unvetted reseller" in text
    assert "Subscriptions (priced separately)" in text and "Z.AI Coding Plan" in text


def test_leaderboard_is_not_ranked_by_model_count():
    db = fresh_db()
    views = analyze.all_views(db, S)
    board = analyze.leaderboard(views, S)
    names = [v["model"]["name"] for v in board["free_models"]]
    assert "Gemini 3.8 Flash" in names and "DeepSeek V4 Flash" in names
    assert all(r["score"] > 0 for r in board["free_providers"])
    assert {r["provider"] for r in board["free_providers"]} >= {"openrouter", "google"}


def test_alternatives_are_free_or_cheaper():
    db = fresh_db()
    views = analyze.all_views(db, S)
    alts = analyze.alternatives(views, "google/gemini-4-argon")
    assert any(a["model"]["id"] == "google/gemini-3-8-flash" for a in alts)
    assert alts and all(a["reason"] in ("free access", "available to use") or "cheaper" in a["reason"] for a in alts)


# ---------------------------------------------------------------------------------------------- database

def test_first_run_is_a_baseline_then_changes_are_recorded():
    db = fresh_db()
    assert db.meta["bootstrapped"] and db.changes == []
    md = models_dev()
    md["togetherai"]["models"]["fable"]["cost"] = {"input": 3, "output": 15}
    md["groq"] = {"id": "groq", "name": "Groq", "doc": "https://groq.com", "models": {
        "llama": listing("Llama 5 70B", 0.5, 0.5, "meta-llama/llama-5-70b")}}
    models, offers = build(md=md)
    events = db.apply(models, offers, {"models_dev", "openrouter", "gemini"})
    kinds = {(e["kind"], e.get("field")) for e in events}
    assert ("changed", "price") in kinds and ("new_offer", None) in kinds
    price = next(e for e in events if e.get("field") == "price")
    assert price["old"]["in"] == 6 and price["new"]["in"] == 3


def test_missing_offers_need_two_loaded_runs_and_failed_sources_change_nothing():
    db = fresh_db()
    md = models_dev()
    del md["togetherai"]["models"]["fable"]
    models, offers = build(md=md)
    db.apply(models, offers, {"openrouter", "gemini"})                       # models.dev failed: nothing concluded
    assert db.offers["togetherai:anthropic/claude-fable-5-2"]["active"] and not db.offers[
        "togetherai:anthropic/claude-fable-5-2"].get("misses")
    db.apply(models, offers, {"models_dev", "openrouter", "gemini"})
    assert db.offers["togetherai:anthropic/claude-fable-5-2"]["active"]      # one miss is not enough
    events = db.apply(models, offers, {"models_dev", "openrouter", "gemini"})
    assert not db.offers["togetherai:anthropic/claude-fable-5-2"]["active"]
    assert any(e["kind"] == "offer_removed" for e in events)


def test_database_round_trip_one_record_per_line_and_no_duplicates(isolated):
    db = fresh_db()
    db.meta["custom"] = 1
    db.save()
    path = isolated / "state" / "modelaccess.json"
    text = path.read_text()
    assert json.loads(text)["meta"]["custom"] == 1
    assert sum(1 for line in text.splitlines() if '"togetherai:anthropic/claude-fable-5-2"' in line) == 1
    again = AccessDB()
    assert again.offers.keys() == db.offers.keys() and again.meta["custom"] == 1
    models, offers = build()
    again.apply(models, offers, {"models_dev"})
    assert len(again.offers) == len(db.offers)                                # merged, not duplicated


# ---------------------------------------------------------------------------------------------- alerts

def paid_to_free_change(db):
    orr = openrouter_rows() + [{"id": "anthropic/claude-fable-5.2:free", "name": "Anthropic: Claude Fable 5.2 (free)",
                                "context_length": 200000, "pricing": {"prompt": "0", "completion": "0"},
                                "architecture": {}, "supported_parameters": ["tools"], "top_provider": {}}]
    models, offers = build(orr=orr)
    return db.apply(models, offers, {"models_dev", "openrouter", "gemini"})


def test_paid_to_free_alert_has_every_field_and_is_never_called_unlimited():
    db = fresh_db()
    events = paid_to_free_change(db)
    cands = al.build(db, events, CFG)
    a = next(c for c in cands if c["model"] == "anthropic/claude-fable-5-2")
    assert a["kind"] == "PAID_TO_FREE" and a["priority"] == "P1"
    assert "paid only" in a["before"] and "50 requests/day" in a["now"]
    parts = al.render(al.fresh(db, cands, CFG))
    text = "\n".join(parts)
    assert all(p.startswith(PREFIX) for p in parts)
    for label in ("Provider:", "What changed:", "Before:", "Now:", "Quota / pricing:", "Source:", "Verification:"):
        assert label in text
    assert "unlimited" not in text.lower()
    assert all(not msg.check_html(p) for p in parts)


def test_unverified_offers_never_alert():
    db = fresh_db()
    md = models_dev()
    md["othercloud"] = {"id": "othercloud", "name": "OtherCloud", "doc": "x", "models": {
        "fable": listing("Claude Fable 5.2", 0, 0, "anthropic/claude-fable-5-2")}}
    models, offers = build(md=md)
    events = db.apply(models, offers, {"models_dev", "openrouter", "gemini"})
    assert any(e["kind"] == "new_offer" for e in events)
    assert al.build(db, events, CFG) == []


def test_duplicate_alerts_are_prevented_across_runs(monkeypatch):
    db = fresh_db()
    events = paid_to_free_change(db)
    first = al.fresh(db, al.build(db, events, CFG), CFG)
    assert first
    al.queue(db, first)
    assert al.fresh(db, al.build(db, events, CFG), CFG) == []      # already queued
    sent = []
    monkeypatch.setattr(deliver, "telegram_configured", lambda: True)
    monkeypatch.setattr("agent.releases.notify.telegram_configured", lambda: True)
    monkeypatch.setattr("agent.releases.notify.send_telegram_message",
                        lambda text, silent=False: sent.append(text) or {"message_id": len(sent), "date": 1})
    monkeypatch.setattr("agent.releases.notify.edit_telegram_message", lambda *a, **k: None)
    assert pipeline.deliver_pending(db) == []
    assert sent and sent[0].startswith(PREFIX)
    assert all(a["key"] in db.alerts for a in first)               # recorded only after Telegram confirmed
    assert al.fresh(db, al.build(db, events, CFG), CFG) == []      # never sent twice


def test_failed_delivery_is_retried_not_marked_sent(monkeypatch):
    db = fresh_db()
    al.queue(db, al.fresh(db, al.build(db, paid_to_free_change(db), CFG), CFG))

    def fail(text, silent=False):
        raise deliver.TelegramError("HTTP 502", 502, True)
    monkeypatch.setattr("agent.releases.notify.telegram_configured", lambda: True)
    monkeypatch.setattr("agent.releases.notify.send_telegram_message", fail)
    assert pipeline.deliver_pending(db)                            # still pending
    assert db.alerts == {}


def test_platform_discontinued_alert():
    p = next(x for x in CFG["platforms"] if x["id"] == "github-models")
    cands = al.build(fresh_db(), [], CFG, [{"platform": p, "old": "LIMITED_FREE", "new": "DISCONTINUED",
                                            "verified": "2026-11-16"}])
    assert cands[0]["kind"] == "DISCONTINUED" and "Retired" in cands[0]["now"]


# ---------------------------------------------------------------------------------------------- commands

@pytest.fixture
def bundle():
    db = fresh_db()
    db.platforms = {"github-copilot-free": {"shown_status": "LIMITED_FREE", "verified": "2026-11-16", "check": "ok"},
                    "github-models": {"shown_status": "DISCONTINUED", "verified": "2026-11-16", "check": "ok"},
                    "omo-dev": {"shown_status": "UNVERIFIED"}, "arena-direct": {"shown_status": "UNVERIFIED"}}
    b, _, _ = respond.build(db, CFG)
    return b


@pytest.mark.parametrize("query", ["", "free", "cheapest", "leaderboard", "help", "weekly", "Claude", "anthropic",
                                   "Claude fable 5.2", "OpenAI gpt-9", "deepseek v4 flash", "gemini 3.8 flash",
                                   "compare deepseek v4 flash", "alternatives claude fable 5.2", "compare",
                                   "nonexistent model xyz", "Gemini"])
def test_every_reply_begins_with_the_identity_line(bundle, query):
    out = respond.route(bundle, query)
    assert out and all(m.startswith(PREFIX + "\n") for m in out)
    assert all(not msg.check_html(m) for m in out)
    assert all(msg.tg_len(m) <= 4096 for m in out)


def test_model_card_has_the_requested_answers(bundle):
    text = "\n".join(respond.route(bundle, "deepseek v4 flash"))
    for label in ("Best free option", "Cheapest verified provider", "Best value for developers", "Best for agents",
                  "Best for heavy usage", "Free access details", "Registration:", "Card required:", "Daily limit:",
                  "Monthly limit:", "Token quota:", "Rate limits:", "Geography:", "Commercial use:",
                  "Permanent or temporary:", "Verification:", "Last checked"):
        assert label in text, label
    assert "not stated" in text                    # unknown terms are said to be unknown, never invented
    assert "UNVERIFIED free listings" in text and "MysteryCloud" in text


def test_company_and_model_routing(bundle):
    assert "ANTHROPIC" in respond.route(bundle, "Claude")[0]
    assert "Claude Fable 5.2" in respond.route(bundle, "claude fable 5.2")[0]
    assert "Claude Fable 5.2" in respond.route(bundle, "Anthropic fable")[0]
    assert "couldn&#x27;t find" in respond.route(bundle, "zzz qqq")[0] or "couldn't find" in respond.route(bundle, "zzz qqq")[0]
    assert "ALTERNATIVES TO CLAUDE FABLE 5.2" in respond.route(bundle, "alternatives claude fable 5.2")[0]


def test_free_view_labels_unverified_claims_and_discontinued(bundle):
    text = "\n".join(respond.route(bundle, "free"))
    confirmed, _, unverified = text.partition("UNVERIFIED claims and community leads")
    assert "unlimited" not in confirmed.lower()
    assert "OMO dev" in unverified and "Not verified" in unverified       # the screenshot's "unlimited" claim
    assert "Arena" in unverified
    assert "GitHub Copilot Free" in confirmed and "Discontinued" in text and "GitHub Models" in text


def test_empty_database_reply():
    out = respond.route({}, "claude")
    assert out[0].startswith(PREFIX) and "hasn't been built yet" in out[0]


def test_free_command_is_unchanged_and_independent():
    out = freemodels.render([("OpenRouter", [{"name": "a/b:free", "url": "https://openrouter.ai/a/b:free",
                                               "context": 8000, "new": False}])])
    assert not out[0].startswith(PREFIX) and "Free models you can use right now" in out[0]


def test_modelaccess_is_registered_and_parsed_with_arguments(monkeypatch, isolated):
    from agent import commands

    assert any(c["command"] == "modelaccess" for c in commands.COMMANDS)
    assert "/modelaccess" in commands.HELP
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    class R:
        ok = True

        def json(self):
            return {"result": [{"update_id": 1, "message": {"chat": {"id": 42}, "text": "/modelaccess claude fable 5.2"}},
                               {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/free"}}]}
    monkeypatch.setattr(commands.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(commands.requests, "post", lambda *a, **k: R())
    asked = []
    monkeypatch.setattr("agent.modelaccess.pipeline.answer", lambda args: asked.append(args) or [PREFIX + "\nok"])
    monkeypatch.setattr(commands, "send_free_models", lambda config: asked.append("FREE"))
    sent = []
    monkeypatch.setattr(commands, "send_telegram", lambda msgs: sent.extend(msgs))
    commands.handle({})
    assert asked == ["claude fable 5.2", "FREE"]
    assert sent == [PREFIX + "\nok"]


def test_command_errors_still_answer_with_the_identity_line(monkeypatch):
    from agent import commands

    def boom(args):
        raise RuntimeError("disk full")
    monkeypatch.setattr("agent.modelaccess.pipeline.answer", boom)
    out = commands.model_access("claude")
    assert out[0].startswith(PREFIX) and "disk full" in out[0]


# ---------------------------------------------------------------------------------------------- sources & refresh

def test_gemini_pricing_parser():
    parsed = src.parse_gemini_pricing(GEMINI_MD)
    assert parsed["gemini-3.8-flash"] == {"name": "Gemini 3.8 Flash", "free": True, "improves_products": True}
    assert parsed["gemini-4-argon"]["free"] is False and parsed["lyria-3-pro-preview"]["free"] is False


def test_retries_then_raises_and_never_retries_a_404(monkeypatch):
    calls = []

    def flaky():
        calls.append(1)
        raise ConnectionError("reset")
    with pytest.raises(ConnectionError):
        src.with_retries(flaky, waits=(0, 0))
    assert len(calls) == 3

    class E(Exception):
        response = type("R", (), {"status_code": 404})()
    calls.clear()

    def missing():
        calls.append(1)
        raise E()
    with pytest.raises(E):
        src.with_retries(missing, waits=(0, 0))
    assert len(calls) == 1


def fake_sources(monkeypatch, md=None, orr=None, fail=()):
    def maybe(name, value):
        def fn(*a, **k):
            if name in fail:
                raise ConnectionError(f"{name} down")
            return value() if callable(value) else value
        return fn
    monkeypatch.setattr(src, "models_dev", maybe("models_dev", lambda: md or models_dev()))
    monkeypatch.setattr(src, "openrouter", maybe("openrouter", lambda: orr or openrouter_rows()))
    monkeypatch.setattr(src, "gemini_pricing", maybe("gemini", lambda: src.parse_gemini_pricing(GEMINI_MD)))
    monkeypatch.setattr(src, "check_page", maybe("pages", True))
    monkeypatch.setattr(src, "openrouter_endpoints", lambda mid: [
        {"provider_name": "Together", "pricing": {"prompt": "0.000006", "completion": "0.00003"}, "uptime_last_1d": 99.5}])
    monkeypatch.setattr(src, "leads", lambda lcfg: [
        {"title": "DeepSeek V4 Flash is free on OpenRouter now", "url": "https://reddit.com/r/LocalLLaMA/1", "sub": "LocalLLaMA",
         "text": ""},
        {"title": "Uncensored heretic model for free", "url": "https://reddit.com/r/x/2", "sub": "x", "text": ""}])


def test_refresh_end_to_end_then_alerts_once(monkeypatch, isolated):
    fake_sources(monkeypatch)
    assert pipeline.refresh() == 0
    db = AccessDB()
    assert db.meta["bootstrapped"] and db.models and db.outbox == {}             # baseline: no alerts
    assert db.platforms["github-models"]["shown_status"] == "DISCONTINUED"
    assert db.meta["verified_rules"]["openrouter"]
    lead = next(iter(db.leads.values()))
    assert len(db.leads) == 1 and lead["status"] == "UNVERIFIED"                 # the uncensored post is dropped
    assert "confirmed free offer" in (lead["confirmed_note"] or "")
    routed = [o for o in db.offers.values() if o.get("routes")]
    assert routed and routed[0]["routes"][0]["name"] == "Together"
    orr = openrouter_rows() + [{"id": "anthropic/claude-fable-5.2:free", "name": "Claude Fable 5.2 (free)",
                                "context_length": 200000, "pricing": {"prompt": "0", "completion": "0"}}]
    fake_sources(monkeypatch, orr=orr)
    assert pipeline.refresh() == 0                                               # Telegram not set up: stays queued
    db = AccessDB()
    assert len(db.outbox) == 1 and next(iter(db.outbox.values()))["status"] == "PENDING"
    assert pipeline.refresh() == 0
    assert len(AccessDB().outbox) == 1                                           # no duplicate batch


def test_refresh_survives_failing_sources(monkeypatch, isolated):
    fake_sources(monkeypatch)
    pipeline.refresh()
    before = AccessDB().offers.keys()
    fake_sources(monkeypatch, fail=("models_dev", "openrouter", "gemini", "pages"))
    assert pipeline.refresh() == 1
    db = AccessDB()
    assert db.offers.keys() == before and all(o["active"] for o in db.offers.values())
    assert db.meta["sources"]["models_dev"]["ok"] is False
    fake_sources(monkeypatch, fail=("openrouter",))
    assert pipeline.refresh() == 0
    db = AccessDB()
    assert db.offers["openrouter:deepseek/deepseek-v4-flash:free"]["active"]   # kept: its catalog didn't load
    bundle, _, _ = respond.build(db, CFG)
    assert "not loaded last time: openrouter" in bundle["views"]["overview"][-1]


def test_answer_builds_the_database_on_first_use(monkeypatch, isolated):
    fake_sources(monkeypatch)
    out = pipeline.answer("deepseek v4 flash")
    assert out[0].startswith(PREFIX) and "DeepSeek V4 Flash" in out[0]
    assert (isolated / "state" / "modelaccess.json").exists()


def test_bot_payloads_cover_every_shard_and_skip_unchanged(monkeypatch):
    db = fresh_db()
    b, _, _ = respond.build(db, CFG)
    payloads = respond.bot_payloads(b)
    assert set(payloads) == {"modelaccess_main", "modelaccess_index"} | {f"modelaccess_s{n}" for n in range(16)}
    assert all(r["i"] in payloads[f"modelaccess_s{r['s']}"] for r in payloads["modelaccess_index"])
    pushed = []
    monkeypatch.setattr("agent.botpush.bot_url", lambda: "https://bot.example")
    monkeypatch.setattr("agent.botpush.push", lambda k, v: pushed.append(k) or True)
    assert pipeline.push_bot(db, b) == 18
    assert pipeline.push_bot(db, b) == 0


# ---------------------------------------------------------------------------------------------- weekly

def test_weekly_report_has_the_opportunities_section(monkeypatch, isolated):
    db = fresh_db()
    paid_to_free_change(db)
    db.save()
    report = pipeline.weekly_report(db, CFG)
    parts = pipeline.render_weekly(report, db, CFG)
    text = "\n".join(parts)
    assert all(p.startswith(PREFIX) for p in parts)
    assert "BEST MODEL ACCESS OPPORTUNITIES THIS WEEK" in text
    assert "Claude Fable 5.2" in text and "new free access on OpenRouter" in text
    sent = []
    monkeypatch.setattr("agent.deliver.deliver", lambda config, m, email, silent=False: sent.append(m) or (["telegram"], []))
    assert pipeline.weekly({"delivery": {"telegram": True}}) == 0
    assert sent and json.loads((isolated / "state" / "last_modelaccess_weekly.json").read_text()) == sent[0]
    assert AccessDB().meta["weekly_last"]
    b, _, _ = respond.build(AccessDB(), CFG)
    assert respond.route(b, "weekly") == sent[0]


def test_weekly_dry_run_sends_nothing(monkeypatch, isolated):
    fresh_db().save()
    monkeypatch.setattr("agent.deliver.deliver", lambda *a, **k: pytest.fail("sent"))
    assert pipeline.weekly({}, dry_run=True) == 0
    assert not (isolated / "state" / "last_modelaccess_weekly.json").exists()
