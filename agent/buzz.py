"""Buzz: every 3 hours, the most talked-about things in AI on GitHub, Hugging Face, Reddit and YouTube,
"something to talk about".

Mainly AI (new models, upgrades, uncensored models, free models, offers, censorship news, trending discussions and
videos), with extra weight on interviews with people who work in AI and on the focus companies in
config.yaml → buzz.focus. Trending GitHub repos are included even when they are not AI.
One AI call picks the best few and writes a talking point for each. Links always come from the items.
"""

import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from . import accuracy, llm
from .builders import _gh_headers, rising_repos, trending_repos
from .common import clean_text, get, now_utc, parse_date
from .digest import split_messages
from .sources import fetch, is_interview, reddit_posts, youtube_channel_id, youtube_videos

KEEP_DAYS = 7  # a pick can come back after a week if it's still the talk of the town
TYPE_ICON = {"interview": "🎙", "new model": "🧠", "upgrade": "⬆️", "uncensored": "🔓", "free": "🆓", "offer": "💸",
             "censorship": "🚫", "repo": "📈", "paper": "📄", "video": "📺", "discussion": "🗨", "news": "🤗"}
# A video from a general channel (a VC firm, a business podcast) only counts when its title is about AI.
AI_TOPIC = re.compile(r"\bA\.?I\b|artificial intelligence|\bAGI\b|\bASI\b|\bLLMs?\b|machine learning|deep learning|"
                      r"neural|chatbot|\bagents?\b|agentic|superintelligen|\brobot|\bGPUs?\b|nvidia|frontier model|"
                      r"\bvibe cod", re.I)
UNCENSORED = re.compile(r"uncensor|abliterat|heretic|unfilter|nsfw|jailbr", re.I)

BUZZ_PROMPT = """You find "something to talk about" for an AI creator, every 3 hours. You get a numbered list of
what is hot right now on GitHub and Hugging Face (trending repos, new and trending models, uncensored models,
Spaces, papers, the Hugging Face blog), on Reddit (today's top posts in the AI subreddits, with upvotes and
comments when known) and on YouTube (new videos from AI podcasts, interview shows and AI news channels, with views),
plus a few news items about free offers and censorship.
Items marked "interview" are interviews, podcasts, AMAs or talks with people who work in AI: founders, CEOs,
researchers, engineers, investors. The creator especially wants these.
Items marked FOCUS are about the companies the creator follows most (Anthropic/Claude, OpenAI/ChatGPT, GLM,
Kimi, MiniMax, xAI/Grok, DeepSeek, Qwen, Google Gemini/Gemma, Meta Llama).

Answer with JSON only:
{"headline": "the one thing to talk about right now, one sentence",
 "picks": [{"item": 1, "type": "interview | new model | upgrade | uncensored | free | offer | censorship | repo | paper | video | discussion | news",
            "what": "one sentence: what it is", "why": "why people are talking about it, with the numbers from the item",
            "talk": "one short line the creator could say or post about it"}]}

Rules:
- Up to 10 picks, most talk-worthy first. AI first, and FOCUS items before others when they are comparably hot.
- Include 2-3 interviews when there are any worth it (most-watched or most-discussed first). For an interview,
  "what" names the guest and their role, and "why" gives the most talk-worthy thing they said or discussed,
  taken from the item text, plus the views or upvotes.
- Include at least 2 GitHub repos, and the hottest Reddit discussion or YouTube video when one stands out.
  A non-AI repo is fine when it is clearly one of the most talked-about today.
- Good picks: new models and model upgrades (above all from FOCUS companies), uncensored/abliterated models,
  models that became free, limited-time offers, censorship or restriction news, repos and papers with big
  momentum (stars today, likes, upvotes).
- Skip "awesome" lists, tutorial-only repos, crypto, spam, re-uploads/quantizations of the same model when the
  original is also listed, memes, help requests, sponsored videos, and anything whose purpose is unclear.
- Use only what the item text says. Do not invent features, numbers or claims."""


def _gh_search(q, per_page=5):
    r = get("https://api.github.com/search/repositories",
            params={"q": q, "sort": "stars", "order": "desc", "per_page": per_page}, headers=_gh_headers())
    return r.json().get("items", [])


def _each(fn, values):
    """Run fn for every value in parallel; one failing value (a renamed org, a hiccup) is skipped."""
    def safe(v):
        try:
            return fn(v)
        except Exception as e:  # noqa: BLE001
            print(f"    skipped {v}: {str(e)[:100]}")
            return []
    with ThreadPoolExecutor(max_workers=4) as pool:
        return [x for got in pool.map(safe, values) for x in got]


def org_repos(orgs, days=3):
    """Repos from the focus orgs that changed in the last few days, most-starred first."""
    since = (now_utc() - timedelta(days=days)).date().isoformat()
    return [{"kind": "repo", "id": f"gh:{r['full_name']}", "name": r["full_name"], "url": r["html_url"],
             "text": clean_text(r.get("description") or "", 300), "score": r.get("stargazers_count", 0),
             "extra": f"{r.get('stargazers_count', 0):,} stars · updated this week"}
            for r in _each(lambda o: _gh_search(f"org:{o} pushed:>{since}"), orgs)]


def github(config):
    cfg = config.get("buzz", {})
    out = []
    for c in trending_repos(ai_only=False):
        out.append({**c, "kind": "repo", "score": c.get("today", 0) * 10,
                    "extra": c["extra"] + ("" if c.get("ai") else " · not AI")})
    for c in rising_repos(cfg.get("topics", ["llm", "ai-agents"]), days=7, min_stars=50):
        out.append({**c, "kind": "repo", "score": c["stars"]})
    return out


def _hf_model(m, label):
    mid = m.get("id") or m.get("modelId")
    created = (m.get("createdAt") or "")[:10]
    bits = [f"{m.get('likes', 0):,} likes"]
    if m.get("downloads"):
        bits.append(f"{m['downloads']:,} downloads")
    if created:
        bits.append(f"created {created}")
    return {"kind": "hf-model", "id": f"hf:{mid}", "name": mid, "url": f"https://huggingface.co/{mid}",
            "text": f"{label} Task: {m.get('pipeline_tag') or 'n/a'}.", "extra": " · ".join(bits),
            "score": int(m.get("trendingScore") or 0) * 20 + m.get("likes", 0)}


def _models(**params):
    return get("https://huggingface.co/api/models", params=params).json()


def hf_trending():
    out = [_hf_model(m, "Trending model on Hugging Face.") for m in _models(sort="trendingScore", limit=30)]
    for s in get("https://huggingface.co/api/spaces", params={"sort": "trendingScore", "limit": 10}).json():
        sid = s.get("id")
        title = (s.get("cardData") or {}).get("title") or ""
        out.append({"kind": "hf-space", "id": f"hfs:{sid}", "name": sid, "url": f"https://huggingface.co/spaces/{sid}",
                    "text": clean_text(f"Trending Hugging Face Space (try it in the browser). {title} "
                                       f"{s.get('shortDescription') or ''}", 300),
                    "extra": f"{s.get('likes', 0):,} likes", "score": int(s.get("trendingScore") or 0) * 20})
    return out


def hf_org_models(orgs, days=7):
    """Newest models from the focus orgs (only the org's own uploads, not re-uploads by others)."""
    cutoff = now_utc() - timedelta(days=days)

    def newest(org):
        return [m for m in _models(author=org, sort="createdAt", direction=-1, limit=8)
                if (parse_date(m.get("createdAt")) or now_utc()) >= cutoff]

    return [_hf_model(m, "New model from a focus lab on Hugging Face.") for m in _each(newest, orgs)]


def hf_uncensored():
    out = {}
    for word in ("uncensored", "abliterated"):
        for m in _models(search=word, sort="trendingScore", limit=8):
            c = _hf_model(m, "Uncensored / abliterated model on Hugging Face.")
            out.setdefault(c["id"], c)
    return list(out.values())


def hf_papers(limit=10):
    out = []
    for p in get("https://huggingface.co/api/daily_papers", params={"limit": 30}).json():
        paper = p.get("paper") or p
        pid, up = paper.get("id"), paper.get("upvotes", 0)
        if not pid:
            continue
        out.append({"kind": "paper", "id": f"paper:{pid}", "name": clean_text(paper.get("title") or p.get("title") or pid, 200),
                    "url": f"https://huggingface.co/papers/{pid}", "text": clean_text(paper.get("summary") or "", 300),
                    "extra": f"{up} upvotes · {p.get('numComments', 0)} comments", "score": up * 5})
    return sorted(out, key=lambda c: -c["score"])[:limit]


def _news(source, kind):
    out = []
    for it in fetch(source, {}):
        out.append({"kind": kind, "id": f"news:{it['id']}", "name": it["title"], "url": it["url"],
                    "text": clean_text(it["summary"], 300), "extra": it["source"] if kind == "hf-blog" else "news",
                    "score": 50, "_item": it})
    return out


def hf_blog():
    return _news({"name": "Hugging Face blog", "type": "rss", "url": "https://huggingface.co/blog/feed.xml"}, "hf-blog")[:8]


def news_net(config):
    """Free offers and censorship news: Hugging Face and GitHub don't publish these, so two news searches."""
    blocked = [b.lower() for b in config.get("blocked_publishers", [])]
    out = []
    for n, query in enumerate(config.get("buzz", {}).get("news_queries", [])):
        got = _news({"name": f"News search {n + 1}", "type": "google_news", "query": query, "tier": "press"}, "news")
        out += [c for c in got[:6] if not accuracy.is_blocked(c["_item"], blocked)]
    for c in out:
        c.pop("_item", None)
    return out


def _ago(hours):
    return f"{int(hours)}h" if hours < 48 else f"{int(hours // 24)} days"


def reddit(config):
    """Today's top posts across the AI subreddits, plus the week's top interviews and AMAs there."""
    rcfg = config.get("buzz", {}).get("reddit") or {}
    subs = rcfg.get("subreddits") or []
    if not subs:
        return []
    posts = [(p, False) for p in reddit_posts(subs, "day", rcfg.get("limit", 50))]
    if rcfg.get("interview_search"):
        time.sleep(10)  # Reddit refuses quick repeat requests
        try:
            posts += [(p, True) for p in reddit_posts(subs, "week", 25, rcfg["interview_search"])]
        except Exception as e:  # noqa: BLE001 - the top posts are still worth sending
            print(f"    Reddit interview search skipped: {str(e)[:100]}")
    out, total = [], len(posts)
    for rank, (p, searched) in enumerate(posts):
        interview = is_interview(p["title"]) or (searched and is_interview(p["text"][:300]))
        if p["score"] is not None:
            extra = f"r/{p['sub']} · {p['score']:,} upvotes · {p['comments'] or 0:,} comments"
            score = p["score"] + 2 * (p["comments"] or 0)
        else:  # RSS: no counts, but the feed is already ordered by upvotes
            extra = f"r/{p['sub']} · " + ("top this week" if searched else f"#{rank + 1} of today's top posts")
            score = (total - rank) * 20
        text = p["text"]
        out.append({"kind": "interview" if interview else "reddit", "id": f"rd:{p['id']}", "name": p["title"],
                    "url": p["url"], "text": clean_text(text, 300) or "(discussion thread)",
                    "extra": extra + (" · interview/AMA" if interview else ""), "score": score})
    return out


def youtube(config, cache):
    """New videos from AI podcasts, interview shows and AI news channels, ranked by views per hour."""
    ycfg = config.get("buzz", {}).get("youtube") or {}
    max_age = timedelta(days=ycfg.get("max_age_days", 4))
    focus = [re.compile(k, re.I) for k in config.get("buzz", {}).get("focus", [])]
    now = now_utc()

    def about_ai(title):
        return bool(AI_TOPIC.search(title)) or any(f.search(title) for f in focus)

    def channel(ch):
        handle = ch.get("handle")
        cid = ch.get("channel_id") or youtube_channel_id(handle, cache)
        try:
            videos = youtube_videos(cid)
        except Exception:
            cache.pop(handle, None)  # both the feed and the page failed: look the id up again next time
            raise
        out = []
        for v in videos:
            if not v["published"] or now - v["published"] > max_age or "#shorts" in v["title"].lower():
                continue
            if ch.get("ai_only") and not about_ai(v["title"]):
                continue
            hours = max(1.0, (now - v["published"]).total_seconds() / 3600)
            interview = bool(ch.get("interview")) or is_interview(v["title"])
            likes = f" · {v['likes']:,} likes" if v["likes"] else ""
            out.append({"kind": "interview" if interview else "youtube", "id": f"yt:{v['id']}", "name": v["title"],
                        "url": v["url"], "text": clean_text(v["description"], 300),
                        "extra": f"{ch.get('name') or v['channel']} on YouTube · {v['views']:,} views in {_ago(hours)}{likes}",
                        "score": int(v["views"] / hours)})
        return out

    return _each(channel, ycfg.get("channels") or [])


def collect(config, youtube_ids=None):
    """youtube_ids: a dict that remembers each channel's id between runs (handle -> id)."""
    cfg = config.get("buzz", {})
    youtube_ids = {} if youtube_ids is None else youtube_ids
    jobs = {
        "github": lambda: github(config),
        "github-labs": lambda: org_repos(cfg.get("gh_orgs", [])),
        "hf-trending": hf_trending,
        "hf-labs": lambda: hf_org_models(cfg.get("hf_orgs", [])),
        "hf-uncensored": hf_uncensored,
        "hf-papers": hf_papers,
        "hf-blog": hf_blog,
        "news": lambda: news_net(config),
        "reddit": lambda: reddit(config),
        "youtube": lambda: youtube(config, youtube_ids),
    }
    candidates, failed = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        for name, fut in futures.items():
            try:
                got = fut.result()
                print(f"  ✓ {name}: {len(got)} candidates")
                candidates += got
            except Exception as e:  # noqa: BLE001 - one broken source never stops the run
                print(f"  ✗ {name}: {str(e)[:150]}")
                failed.append(name)
    for c in candidates:
        c.pop("_item", None)
    focus = [re.compile(k, re.I) for k in cfg.get("focus", [])]
    unique = {}
    for c in candidates:
        text = f"{c['name']} {c['text']}"
        c["focus"] = any(p.search(text) for p in focus)
        c["uncensored"] = bool(UNCENSORED.search(text))
        if c["id"] in unique:  # same repo/model from two places: keep the higher score
            unique[c["id"]]["score"] = max(unique[c["id"]]["score"], c["score"])
        else:
            unique[c["id"]] = c
    return interleave(unique.values()), failed


def interleave(candidates):
    """Scores from different sites can't be compared, so take the best of each kind in turn
    (focus companies first within a kind). The AI sees a varied list even when it's cut short."""
    by_kind = {}
    for c in sorted(candidates, key=lambda c: (not c["focus"], -c["score"])):
        by_kind.setdefault(c["kind"], []).append(c)
    out, queues = [], list(by_kind.values())
    while queues:
        out += [q.pop(0) for q in queues]
        queues = [q for q in queues if q]
    return out


def _line(n, c):
    tags = [c["kind"]] + (["FOCUS"] if c.get("focus") else []) + (["uncensored"] if c.get("uncensored") else [])
    return f"[{n}] ({' · '.join(tags)}) {c['name']} · {c['extra']} :: {c['text']}"


def _fallback(candidates, max_picks):
    kind_type = {"paper": "paper", "hf-blog": "news", "news": "news", "hf-model": "new model", "hf-space": "news",
                 "interview": "interview", "youtube": "video", "reddit": "discussion"}
    top = candidates[:max_picks]  # already interleaved, best of each kind first
    return [(c, "uncensored" if c.get("uncensored") else kind_type.get(c["kind"], "repo"), c["text"], c["extra"], "")
            for c in top]


def pick(config, candidates):
    """Returns (headline, [(candidate, type, what, why, talk)], model)."""
    max_picks = config.get("buzz", {}).get("max_picks", 8)

    def build(max_items=None, **_):
        return "\n".join(_line(n, c) for n, c in enumerate(candidates[: max_items or 90], 1))
    try:
        answer, model = llm.complete(config, BUZZ_PROMPT, build)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip())
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        picks = []
        for p in data.get("picks") or []:
            try:
                n = int(p.get("item"))
            except (TypeError, ValueError):
                continue
            if not 1 <= n <= len(candidates) or any(candidates[n - 1] is q[0] for q in picks):
                continue  # the AI cited an item that doesn't exist, or twice
            kind = str(p.get("type", "")).strip().lower()
            picks.append((candidates[n - 1], kind if kind in TYPE_ICON else "news",
                          clean_text(str(p.get("what", "")), 250), clean_text(str(p.get("why", "")), 250),
                          clean_text(str(p.get("talk", "")), 250)))
        if picks:
            return clean_text(str(data.get("headline", "")), 250), picks[:max_picks], model
    except Exception as e:  # noqa: BLE001 - always send something, even without AI
        print(f"AI picking failed, using the hottest items instead: {str(e)[:200]}")
    return "", _fallback(candidates, max_picks), "none"


def more_interviews(candidates, picks, want=3):
    """Interviews the AI didn't pick, so every Buzz has a few when there are any (best first)."""
    picked = {c["id"] for c, kind, *_ in picks}
    have = sum(1 for c, kind, *_ in picks if kind == "interview" or c["kind"] == "interview")
    rest = [c for c in candidates if c["kind"] == "interview" and c["id"] not in picked]
    return sorted(rest, key=lambda c: (not c.get("focus"), -c["score"]))[: max(0, want - have)]


def render(headline, picks, model, failed, scanned, interviews=()):
    e = html.escape
    blocks = [f"<b>🔥 Buzz: what everyone's talking about</b> · {now_utc():%a %d %b, %H:%M} UTC"
              + (f"\n<i>{e(headline)}</i>" if headline else "")]
    for i, (c, kind, what, why, talk) in enumerate(picks, 1):
        text = f"\n{i}. {TYPE_ICON.get(kind, '•')} <a href=\"{e(c['url'], quote=True)}\"><b>{e(c['name'])}</b></a>"
        if c["extra"]:
            text += f" · <i>{e(c['extra'])}</i>"
        if what:
            text += f"\n{e(what)}"
        if why and why != c["extra"]:
            text += f"\n🗣 {e(why)}"
        if talk:
            text += f"\n💬 <i>{e(talk)}</i>"
        blocks.append(text)
    if interviews:
        lines = [f"• <a href=\"{e(c['url'], quote=True)}\">{e(c['name'])}</a> · <i>{e(c['extra'])}</i>"
                 for c in interviews]
        blocks.append("\n<b>🎙 More interviews worth watching</b>\n" + "\n".join(lines))
    footer = f"From {scanned} hot items on GitHub, Hugging Face, Reddit and YouTube · picked by {model}"
    if failed:
        footer += f" · couldn't reach: {', '.join(failed)}"
    blocks.append(f"\n<i>{e(footer)}</i>")
    return split_messages(blocks)
