"""Builder picks: a daily list of useful, trending tools for people who build with AI.

Candidates come from GitHub Trending, fast-rising new GitHub repos, Hugging Face trending models and
Spaces, and Show HN launches. One AI call picks the best ~10 and says what each does and who it helps.
"""

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from bs4 import BeautifulSoup

from . import llm
from .common import ROOT, clean_text, env, get, now_utc
from .digest import split_messages

AI_WORDS = re.compile(
    r"\b(ai|llms?|gpt|agents?|agentic|mcp|claude|openai|gemini|grok|glm|rag|models?|inference|diffusion|"
    r"copilot|codex|prompts?|embeddings?|transformers?|ml|neural|chatbot|ollama|vllm|lora|fine-?tun\w*|"
    r"voice|speech|vision|multimodal|coding assistant|code review)\b", re.I)
KIND_ICON = {"rising": "🚀", "trending": "📈", "hf-model": "🤗", "hf-space": "🤗", "show-hn": "🟧"}
LAST_REPOS = ROOT / "state" / "last_repos.json"

BUILDER_PROMPT = """You pick today's most useful new tools for people who build with AI (developers, indie
hackers, creators who automate). You get a numbered list of trending GitHub repos, Hugging Face models and
Spaces, and Show HN launches.

Answer with JSON only:
{"theme": "one sentence on what builders are excited about today",
 "picks": [{"item": 1, "what": "one sentence: what it does", "who": "who it helps and for what"}]}

Rules:
- Pick up to 10 items, most useful first. Prefer real tools people can use now: agents, coding tools, MCP
  servers, local or open models, RAG, evals, voice, automation, infrastructure.
- Skip "awesome" lists, tutorials-only repos, crypto, spam, and anything whose purpose is unclear.
- Use only what the item text says. Do not invent features or numbers."""


def _gh_headers():
    headers = {"Accept": "application/vnd.github+json"}
    if env("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {env('GITHUB_TOKEN')}"
    return headers


def rising_repos(topics, days=7, min_stars=30):
    since = (now_utc() - timedelta(days=days)).date().isoformat()

    def search(topic):
        q = f"topic:{topic} created:>{since} stars:>{min_stars}"
        r = get("https://api.github.com/search/repositories", params={"q": q, "sort": "stars", "order": "desc",
                                                                     "per_page": 10}, headers=_gh_headers())
        return r.json().get("items", [])

    found = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for repos in pool.map(search, topics):
            for r in repos:
                found[r["full_name"]] = {
                    "kind": "rising", "id": f"gh:{r['full_name']}", "name": r["full_name"], "url": r["html_url"],
                    "text": clean_text(r.get("description") or "", 300), "stars": r.get("stargazers_count", 0),
                    "extra": f"new this week · {r.get('language') or 'n/a'}",
                }
    return list(found.values())


def trending_repos():
    soup = BeautifulSoup(get("https://github.com/trending?since=daily").text, "html.parser")
    out = []
    for row in soup.select("article.Box-row"):
        link = row.select_one("h2 a")
        if not link:
            continue
        name = re.sub(r"\s+", "", link.get_text())
        desc = clean_text((row.select_one("p") or BeautifulSoup("", "html.parser")).get_text(" ", strip=True), 300)
        if not AI_WORDS.search(f"{name} {desc}"):
            continue
        today = re.search(r"([\d,]+) stars? today", row.get_text(" ", strip=True))
        total = row.select_one('a[href$="/stargazers"]')
        lang = row.select_one('[itemprop="programmingLanguage"]')
        out.append({
            "kind": "trending", "id": f"gh:{name}", "name": name, "url": f"https://github.com/{name}", "text": desc,
            "stars": int(re.sub(r"\D", "", total.get_text()) or 0) if total else 0,
            "extra": f"+{today.group(1)} stars today · {lang.get_text(strip=True) if lang else 'n/a'}" if today else "",
        })
    return out


def hf_trending(limit_models=12, limit_spaces=8):
    out = []
    for m in get("https://huggingface.co/api/models", params={"sort": "trendingScore", "limit": limit_models}).json():
        mid = m.get("id") or m.get("modelId")
        out.append({"kind": "hf-model", "id": f"hf:{mid}", "name": mid, "url": f"https://huggingface.co/{mid}",
                    "text": f"Hugging Face model. Task: {m.get('pipeline_tag') or 'n/a'}.", "stars": m.get("likes", 0),
                    "extra": f"{m.get('likes', 0)} likes"})
    for s in get("https://huggingface.co/api/spaces", params={"sort": "trendingScore", "limit": limit_spaces}).json():
        sid = s.get("id")
        title = (s.get("cardData") or {}).get("title") or ""
        out.append({"kind": "hf-space", "id": f"hfs:{sid}", "name": sid, "url": f"https://huggingface.co/spaces/{sid}",
                    "text": clean_text(f"Hugging Face Space (try it in the browser). {title} {s.get('shortDescription') or ''}", 300),
                    "stars": s.get("likes", 0), "extra": f"{s.get('likes', 0)} likes"})
    return out


def show_hn(min_points=40):
    since = int((now_utc() - timedelta(hours=48)).timestamp())
    params = {"tags": "show_hn", "hitsPerPage": 40, "numericFilters": f"points>={min_points},created_at_i>{since}"}
    out = []
    for h in get("https://hn.algolia.com/api/v1/search_by_date", params=params).json().get("hits", []):
        title = h.get("title", "")
        if not AI_WORDS.search(title):
            continue
        out.append({"kind": "show-hn", "id": f"hn:{h['objectID']}", "name": title.replace("Show HN:", "").strip(),
                    "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                    "text": f"Launched on Show HN: {title}", "stars": h.get("points", 0),
                    "extra": f"{h.get('points', 0)} points on HN"})
    return out


def collect(config):
    cfg = config.get("builders", {})
    jobs = {
        "rising": lambda: rising_repos(cfg.get("topics", ["llm", "ai-agents", "mcp"])),
        "trending": trending_repos,
        "huggingface": hf_trending,
        "show-hn": show_hn,
    }
    candidates, failed = [], []
    for name, job in jobs.items():
        try:
            got = job()
            print(f"  ✓ {name}: {len(got)} candidates")
            candidates += got
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {name}: {str(e)[:150]}")
            failed.append(name)
    unique = {}
    for c in candidates:
        unique.setdefault(c["id"], c)
    return list(unique.values()), failed


def pick(config, candidates):
    """Returns (theme, [(candidate, what, who)], model)."""
    def build(max_items=None, **_):
        lines = []
        for n, c in enumerate(candidates[: max_items or 60], 1):
            lines.append(f"[{n}] ({c['kind']}) {c['name']} · {c['extra']} :: {c['text']}")
        return "\n".join(lines)
    try:
        answer, model = llm.complete(config, BUILDER_PROMPT, build)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip())
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        picks = []
        for p in data.get("picks") or []:
            try:
                n = int(p.get("item"))
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(candidates) and all(candidates[n - 1] is not q[0] for q in picks):
                picks.append((candidates[n - 1], clean_text(str(p.get("what", "")), 250), clean_text(str(p.get("who", "")), 200)))
        if picks:
            return clean_text(str(data.get("theme", "")), 250), picks[:10], model
    except Exception as e:  # noqa: BLE001
        print(f"AI picking failed, using the most-starred instead: {str(e)[:200]}")
    top = sorted(candidates, key=lambda c: -c["stars"])[:10]
    return "", [(c, c["text"], "") for c in top], "none"


def render(theme, picks, model, failed):
    e = html.escape
    blocks = [f"<b>🛠 Builder picks</b> · {now_utc():%a %d %b}" + (f"\n<i>{e(theme)}</i>" if theme else "")]
    for i, (c, what, who) in enumerate(picks, 1):
        text = f"\n{i}. {KIND_ICON.get(c['kind'], '•')} <a href=\"{e(c['url'], quote=True)}\"><b>{e(c['name'])}</b></a>"
        if c["extra"]:
            text += f" · <i>{e(c['extra'])}</i>"
        if what:
            text += f"\n{e(what)}"
        if who:
            text += f"\n<i>For: {e(who)}</i>"
        blocks.append(text)
    footer = f"🚀 rising new repo · 📈 GitHub trending · 🤗 Hugging Face · 🟧 Show HN · picked by {model}"
    if failed:
        footer += f" · couldn't reach: {', '.join(failed)}"
    blocks.append(f"\n<i>{e(footer)}</i>")
    return split_messages(blocks)
