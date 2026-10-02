"""Entry point.

    python -m agent digest          collect news, summarise with AI, send the brief (every 6 hours)
    python -m agent alerts          quick check of official sources, instant ping, no AI (hourly)
    python -m agent builders        daily Builder picks: trending AI tools and repos
    python -m agent weekly          Sunday recap of the week's biggest stories
    python -m agent check-sources   test every source and print which ones work
    python -m agent test-delivery   send a test message to Telegram / email
    python -m agent test-ai         check every AI key with a tiny request
    python -m agent free-models     send the list of models that are free right now to Telegram

Add --dry-run to print instead of sending and to leave the saved state untouched.
"""

import argparse
import html
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from . import accuracy, botpush, builders, commands, freemodels, llm, weekly
from .common import ROOT, TIER_RANK, load_yaml, now_utc, parse_date
from .deliver import deliver, email_configured, telegram_configured
from .factcheck import factcheck
from .digest import (SYSTEM_PROMPT, build_user_message, fallback_brief, parse_answer, rank_items,
                     render_email_html, render_markdown, render_telegram)
from .sources import fetch
from .state import State

FAILING_WARN_AFTER = 3  # runs in a row before a broken source is mentioned in the brief


def load():
    return load_yaml("config.yaml"), load_yaml("sources.yaml")["sources"]


def company_matchers(config):
    return {
        key: [re.compile(k, re.I) for k in c.get("keywords", [])]
        for key, c in config["companies"].items()
    }


def tag_companies(item, matchers):
    text = f"{item['title']} {item['summary']}"
    found = list(item["companies"])
    for key, patterns in matchers.items():
        if key not in found and any(p.search(text) for p in patterns):
            found.append(key)
    item["companies"] = found
    return bool(found)


def fetch_all(sources, state):
    """Fetch every source in parallel. Returns {source name: items or Exception}."""
    def run(src):
        try:
            return src["name"], fetch(src, state.source(src["name"]))
        except Exception as e:  # noqa: BLE001 - one broken source must never stop the run
            return src["name"], e

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(run, sources))


def collect(sources, state, config):
    """Fetch, keep items about our companies, drop ones already seen or too old."""
    matchers = company_matchers(config)
    now = now_utc()
    max_age = timedelta(hours=config.get("max_age_hours", 48))
    first_window = timedelta(hours=config.get("first_run_hours", 12))
    blocked = [b.lower() for b in config.get("blocked_publishers", [])]
    results = fetch_all(sources, state)
    new_items, failed, scanned = [], [], 0

    for src in sources:
        name, result = src["name"], results[src["name"]]
        if isinstance(result, Exception):
            print(f"  ✗ {name}: {str(result)[:160]}")
            state.record_result(name, ok=False)
            failed.append(name)
            continue
        state.record_result(name, ok=True)
        scanned += len(result)
        first_run = state.is_new_source(name)
        exclude = re.compile(src["exclude"], re.I) if src.get("exclude") else None
        fresh = []
        for item in result:
            if exclude and exclude.search(item["title"]):
                continue
            if not tag_companies(item, matchers) or accuracy.is_blocked(item, blocked):
                continue
            if not state.observe(item["id"]):
                continue
            published = parse_date(item["published"])
            if published and now - published > max_age:
                continue
            if first_run and not (published and now - published <= first_window):
                continue  # first look at this source: quietly learn what's already there
            fresh.append(item)
        state.mark_initialized(name)
        fresh = fresh[: src.get("limit", 15)]
        if fresh:
            print(f"  ✓ {name}: {len(fresh)} new")
        new_items.extend(fresh)

    # The same link from two sources: keep the copy from the more authoritative source.
    by_url = {}
    for it in sorted(new_items, key=lambda i: TIER_RANK.get(i["tier"], 9)):
        key = it["url"] or it["id"]
        if key in by_url:
            by_url[key]["companies"] = list(dict.fromkeys(by_url[key]["companies"] + it["companies"]))
        else:
            by_url[key] = it
    return list(by_url.values()), failed, scanned


def failing_warning(state):
    broken = sorted(n for n, count in state.failures.items() if count >= FAILING_WARN_AFTER)
    if not broken:
        return None
    return f"Not working for {FAILING_WARN_AFTER}+ runs (check the URL in sources.yaml): " + ", ".join(broken)


def finish(state, sent, errors, dry_run):
    for e in errors:
        print(f"  Delivery failed: {e}")
    if dry_run:
        print("\n(dry run: nothing sent, state not saved)")
        return 0
    if errors and not sent:
        print("Nothing was delivered, so items stay unread for the next run.")
        return 1
    if not sent:
        print("No delivery channel is set up (TELEGRAM_* or EMAIL_* secrets). Printed above only.")
    state.save()
    return 0


def cmd_digest(args):
    config, sources = load()
    answer_commands(config, args)
    state = State("digest")
    now = now_utc()
    print(f"Collecting from {len(sources)} sources…")
    items, failed, scanned = collect(sources, state, config)
    companies = config["companies"]
    dcfg = config.get("digest", {})
    items = rank_items(items)[: dcfg.get("max_items", 80)]
    when = f"{now:%a %d %b, %H:%M} UTC"
    warning = failing_warning(state)
    print(f"{len(items)} new items, {len(failed)} sources failed.")

    if not items:
        if not dcfg.get("send_when_empty", True):
            return finish(state, ["nothing to send"], [], args.dry_run)
        text = f"🛰 AI News Brief · {when}\nNothing new about your companies since the last run ({len(sources) - len(failed)} sources checked)."
        if warning:
            text += f"\n⚠️ {warning}"
        print("\n" + text)
        if args.dry_run:
            return 0
        sent, errors = deliver(config, [text], ("AI News Brief: quiet period", f"<p>{text}</p>", text))
        return finish(state, sent, errors, args.dry_run)

    if dcfg.get("read_articles", 15):
        accuracy.add_articles(items, dcfg.get("read_articles", 15), dcfg.get("article_chars", 2500))
    recent = accuracy.recent_headlines(state)
    model_used = "none"
    try:
        def build_user(max_items=None, summary_chars=None, article_chars=1500):
            subset = items[:max_items] if max_items else items
            return build_user_message(subset, now, summary_chars or 400, article_chars, recent)

        answer, model_used = llm.complete(config, SYSTEM_PROMPT, build_user)
        brief = parse_answer(answer, items, companies)
    except Exception as e:  # noqa: BLE001 - always send something, even without AI
        print(f"AI summary failed, sending headlines instead: {str(e)[:400]}")
        brief = fallback_brief(items, companies)
    brief, checked = factcheck(config, brief, items, model_used)

    meta = {
        "when": when,
        "footer": (f"{len(items)} new items from {scanned} scanned across {len(sources) - len(failed)} sources"
                   f" · written by {model_used} · {checked}"),
        "warning": warning,
    }
    tg = render_telegram(brief, items, companies, meta)
    md = render_markdown(brief, items, companies, meta)
    subject = f"AI News Brief: {brief['headline'][:90]}"
    print("\n" + md)

    if args.dry_run:
        return finish(state, [], [], True)
    sent, errors = deliver(config, tg, (subject, render_email_html(brief, items, companies, meta), md))
    if "telegram" in sent:
        commands.save_last_brief(tg)  # for the /latest command
    if sent:
        accuracy.remember_headlines(state, brief)
    if dcfg.get("archive", True) and (sent or not errors):
        path = ROOT / "digests" / f"{now:%Y-%m-%d_%H%M}.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(md, encoding="utf-8")
    if sent:
        botpush.push("last_brief", tg)
        botpush.push("briefs", commands.recent_briefs())
    return finish(state, sent, errors, args.dry_run)


def answer_commands(config, args):
    if args.dry_run:
        return
    try:
        commands.handle(config)
    except Exception as e:  # noqa: BLE001 - a command problem must never block the news
        print(f"Telegram commands failed: {str(e)[:200]}")


def refresh_bot_free_models(config, args):
    """Hourly: give the always-on bot a fresh free-model list for instant /free replies."""
    if args.dry_run or not botpush.bot_url():
        return
    try:
        botpush.push("free_models", freemodels.render(freemodels.collect(config)))
    except Exception as e:  # noqa: BLE001
        print(f"  Free-model list for the bot failed: {str(e)[:150]}")


def cmd_alerts(args):
    config, sources = load()
    answer_commands(config, args)
    refresh_bot_free_models(config, args)
    sources = [s for s in sources if s.get("alert")]
    state = State("alerts")
    items, _, _ = collect(sources, state, config)
    items = rank_items(items)[: config.get("alerts", {}).get("max_per_run", 8)]
    print(f"{len(items)} new items for instant alerts.")
    if not items:
        return finish(state, ["nothing to send"], [], args.dry_run)
    lines = ["<b>🚨 Just in</b>"]
    for it in items:
        title = html.escape(it["title"])
        link = f'<a href="{html.escape(it["url"], quote=True)}">{title}</a>' if it["url"] else title
        lines.append(f"• <b>{html.escape(it['source'])}</b>: {link}")
    text = "\n".join(lines)
    print(text)
    if args.dry_run:
        return finish(state, [], [], True)
    channels = ["telegram"] if telegram_configured() else ["email"]
    plain = re.sub(r"<[^>]+>", "", text) + "\n\n" + "\n".join(f"{i['title']}: {i['url']}" for i in items)
    sent, errors = deliver(config, [text], (f"🚨 {items[0]['title'][:80]}", text.replace("\n", "<br>"), plain), channels)
    return finish(state, sent, errors, args.dry_run)


def cmd_check_sources(args):
    config, sources = load()
    scratch = State("check-sources-scratch")  # throwaway, never saved
    results = fetch_all(sources, scratch)
    ok = 0
    for src in sources:
        r = results[src["name"]]
        if isinstance(r, Exception):
            print(f"✗ FAIL  {src['name']:<34} {str(r)[:110]}")
        else:
            ok += 1
            dates = sorted((i["published"] for i in r if i["published"]), reverse=True)
            newest = dates[0][:10] if dates else "-"
            note = " (page_diff: learns the page on first run)" if src["type"] == "page_diff" else ""
            print(f"✓ ok    {src['name']:<34} {len(r):>3} items, newest {newest}{note}")
    print(f"\n{ok}/{len(sources)} sources working.")
    gn = next((i for src in sources if src["type"] == "google_news" and not isinstance(results[src["name"]], Exception)
               for i in results[src["name"]]), None)
    if gn:
        try:
            real = accuracy.real_url(gn["url"])
            print(f"Google News link decoding: works → {real[:80]}")
        except Exception as e:  # noqa: BLE001
            print(f"Google News link decoding: FAILED ({str(e)[:200]})")
    keys = [n for n, p in config["llm"]["providers"].items() if os.environ.get(p["key_env"])]
    print(f"AI providers with a key: {', '.join(keys) or 'none'}")
    print(f"Telegram set up: {telegram_configured()} · Email set up: {email_configured()}")
    return 0


def cmd_test_ai(args):
    """Send a tiny request to every AI provider that has a key, one by one, and report which work."""
    config, _ = load()
    llm_cfg = config["llm"]
    working = 0
    for name in llm_cfg["order"]:
        p = llm_cfg["providers"].get(name)
        if not p or not os.environ.get(p["key_env"]):
            print(f"-  {name:<11} no key ({p['key_env'] if p else '?'} not set)")
            continue
        single = {"llm": {"order": [name], "providers": {name: p}}}
        try:
            text, used = llm.complete(single, 'Reply with the JSON {"ok": true} and nothing else.', lambda **kw: "Test.")
            working += 1
            print(f"✓  {name:<11} works ({used}): {text.strip()[:60]}")
        except Exception as e:  # noqa: BLE001
            print(f"✗  {name:<11} failed: {str(e)[:300]}")
    print(f"\n{working} AI provider(s) working.")
    return 0 if working else 1


def cmd_free_models(args):
    """Send the list of models you can use for free right now to Telegram (or print it)."""
    config, _ = load()
    messages = freemodels.render(freemodels.collect(config))
    for m in messages:
        print(re.sub(r"<[^>]+>", "", m) + "\n")
    if args.dry_run or not telegram_configured():
        return 0
    sent, errors = deliver(config, messages, None, ["telegram"])
    for e in errors:
        print(f"Failed: {e}")
    return 0 if sent else 1


def cmd_builders(args):
    """Daily Builder picks: trending, useful AI tools for builders."""
    config, _ = load()
    answer_commands(config, args)
    state = State("builders")
    print("Collecting builder candidates…")
    candidates, failed = builders.collect(config)
    fresh = [c for c in candidates if c["id"] not in state.seen]  # don't repeat a pick within 30 days
    print(f"{len(fresh)} new candidates ({len(candidates) - len(fresh)} already picked recently).")
    if not fresh:
        return finish(state, ["nothing to send"], [], args.dry_run)
    theme, picks, model = builders.pick(config, fresh)
    messages = builders.render(theme, picks, model, failed)
    for m in messages:
        print(re.sub(r"<[^>]+>", "", m) + "\n")
    if args.dry_run:
        return finish(state, [], [], True)
    sent, errors = deliver(config, messages, None, ["telegram"])
    if sent:
        for c, _, _ in picks:
            state.observe(c["id"])
        commands.save_json(commands.LAST_REPOS, messages)  # for the /repos command
        botpush.push("last_repos", messages)
    return finish(state, sent, errors, args.dry_run)


def cmd_weekly(args):
    """Sunday recap of the week's biggest stories, from the saved briefs."""
    config, _ = load()
    stories = weekly.read_week()
    print(f"{len(stories)} stories in this week's briefs.")
    if not stories:
        print("No briefs saved this week yet.")
        return 0
    headline, picked, ideas, model = weekly.recap(config, stories)
    messages = weekly.render(headline, picked, ideas, model, len(stories))
    for m in messages:
        print(re.sub(r"<[^>]+>", "", m) + "\n")
    if args.dry_run:
        return 0
    sent, errors = deliver(config, messages, (f"Weekly AI recap: {headline[:80]}", "<br>".join(messages),
                                              "\n\n".join(re.sub(r"<[^>]+>", "", m) for m in messages)))
    for e in errors:
        print(f"Failed: {e}")
    return 0 if sent or not errors else 1


def cmd_push_bot(args):
    """Send everything the always-on bot needs (used right after the bot is deployed)."""
    import json

    config, _ = load()
    if not botpush.bot_url():
        print("The bot isn't deployed yet (no bot/url.txt).")
        return 1
    ok = botpush.push("briefs", commands.recent_briefs())
    for key, path in (("last_brief", commands.LAST_BRIEF), ("last_repos", commands.LAST_REPOS)):
        if path.exists():
            ok = botpush.push(key, json.loads(path.read_text(encoding="utf-8"))) and ok
    refresh_bot_free_models(config, args)
    return 0 if ok else 1


def cmd_test_delivery(args):
    config, _ = load()
    text = "✅ Your AI news agent is connected. Briefs will arrive here."
    sent, errors = deliver(config, [text], ("AI News Agent: test message", f"<p>{text}</p>", text))
    print(f"Sent to: {sent or 'nothing'}")
    for e in errors:
        print(f"Failed: {e}")
    if not telegram_configured() and not email_configured():
        print("No channel is set up. Add TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID and/or EMAIL_ADDRESS + EMAIL_APP_PASSWORD.")
    return 0 if sent else 1


def main():
    parser = argparse.ArgumentParser(prog="python -m agent", description="AI news agent")
    parser.add_argument("command", choices=["digest", "alerts", "builders", "weekly", "check-sources", "test-delivery",
                                            "test-ai", "free-models", "push-bot"])
    parser.add_argument("--dry-run", action="store_true", help="print instead of sending; don't save state")
    args = parser.parse_args()
    commands = {"digest": cmd_digest, "alerts": cmd_alerts, "check-sources": cmd_check_sources,
                "test-delivery": cmd_test_delivery, "test-ai": cmd_test_ai,
                "free-models": cmd_free_models, "builders": cmd_builders, "weekly": cmd_weekly,
                "push-bot": cmd_push_bot}
    sys.exit(commands[args.command](args))


if __name__ == "__main__":
    main()
