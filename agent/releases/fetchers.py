"""Reads one source from releases.yaml with the method that suits it. RSS feeds and news pages reuse the
fetchers in agent/sources.py; documentation pages, APIs and repositories are read here and handed to the
parsers in agent/releases/parsers.py."""

import re

import feedparser

from .. import sources as news_sources
from ..common import clean_text, env, get
from . import parsers
from .identity import extract_models, is_quantized


class Skipped(Exception):
    """The source needs a secret that isn't set: skipped quietly, not counted as a failure."""


def _announcement(source):
    """Posts from a blog or news page, via the news fetchers. Category pages are dropped with exclude_url."""
    src = {"name": source["name"], "type": source["type"], "url": source["url"], "tier": "official",
           "company": source.get("company")}
    if source.get("pattern"):
        src["pattern"] = source["pattern"]
    items = news_sources.fetch(src, {})
    if source.get("exclude_url"):
        drop = re.compile(source["exclude_url"])
        items = [i for i in items if not drop.search(i["url"])]
    return items


def _gh_headers():
    from ..builders import _gh_headers as headers

    return headers()


def fetch_source(source, company_cfg):
    """Returns {"kind": role, "entries": [...]} for a company source. Raises on failure, Skipped when a needed
    secret isn't set."""
    role, kind = source["role"], source["type"]
    if source.get("requires_env") and not env(source["requires_env"]):
        raise Skipped(f"{source['requires_env']} not set")
    if role == "announcement":
        return {"kind": role, "entries": _announcement(source)}
    if kind == "huggingface_org":
        data = get("https://huggingface.co/api/models",
                   params={"author": source["org"], "sort": "createdAt", "direction": -1, "limit": 30}).json()
        return {"kind": role, "entries": parsers.huggingface_org_models(data, company_cfg, source["org"])}
    if kind == "github_org":
        data = get(f"https://api.github.com/orgs/{source['org']}/repos",
                   params={"sort": "created", "direction": "desc", "per_page": 30}, headers=_gh_headers()).json()
        return {"kind": role, "entries": parsers.github_org_repos(data, company_cfg, source["org"])}
    parser = source["parser"]
    if kind == "json_api":
        if parser == "gemini_api_models":
            data = get(source["url"], params={"key": env("GEMINI_API_KEY"), "pageSize": 1000}).json()
        elif parser == "anthropic_models_api":
            data = get(source["url"], params={"limit": 1000},
                       headers={"x-api-key": env("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"}).json()
        else:
            data = get(source["url"]).json()
        entries = parsers.CATALOG_PARSERS[parser](data, company_cfg)
    elif kind == "rss":
        feed = feedparser.parse(get(source["url"]).content)
        if feed.bozo and not feed.entries:
            raise ValueError(f"not a valid feed ({feed.bozo_exception})")
        entries = parsers.CHANGELOG_PARSERS[parser](feed.entries, company_cfg, url=source["url"])
    else:
        text = get(source["url"]).text
        if role == "catalog":
            entries = parsers.CATALOG_PARSERS[parser](text, company_cfg)
        else:
            entries = parsers.CHANGELOG_PARSERS[parser](text, company_cfg, url=source["url"])
    if not entries:
        # An official page that suddenly lists nothing has changed layout: fail loudly instead of going blind.
        raise ValueError(f"the {parser} parser found nothing (page layout changed?)")
    return {"kind": role, "entries": entries}


def fetch_detail(source, model_id):
    """The per-model page of a catalog (detail_url) parsed into specs. {} when there is none or it fails."""
    template, parser = source.get("detail_url"), parsers.DETAIL_PARSERS.get(source.get("parser"))
    if not template or not parser:
        return {}
    try:
        return parser(get(template.format(id=model_id), timeout=15).text)
    except Exception as e:  # noqa: BLE001 - specs are a bonus; the release is still reported without them
        print(f"    no detail page for {model_id}: {str(e)[:80]}")
        return {}


def hf_model_card(repo_id, chars=6000):
    """The README (model card) an organisation published with its Hugging Face release."""
    try:
        return clean_text(get(f"https://huggingface.co/{repo_id}/raw/main/README.md", timeout=15).text, chars)
    except Exception:  # noqa: BLE001
        return ""


def fetch_early(signal, companies):
    """Third-party listings. Returns entries tagged with the company they seem to belong to."""
    kind = signal["type"]
    out = []
    if kind == "openrouter":
        prefixes = signal.get("prefixes") or {}
        for m in get(signal["url"]).json().get("data", []):
            mid = m.get("id", "")
            company = next((c for p, c in prefixes.items() if mid.startswith(p)), None)
            if not company or ":" in mid or is_quantized(mid):
                continue
            out.append({"uid": mid, "company": company, "model_ids": [mid.split("/", 1)[1]],
                        "title": f"OpenRouter lists {m.get('name') or mid}", "url": f"https://openrouter.ai/{mid}",
                        "text": clean_text(m.get("description") or "", 400)})
    elif kind == "models_dev":
        data = get(signal["url"]).json()
        for provider, company in (signal.get("providers") or {}).items():
            for mid, m in ((data.get(provider) or {}).get("models") or {}).items():
                if not isinstance(m, dict):
                    continue
                out.append({"uid": f"{provider}/{mid}", "company": company, "model_ids": [mid],
                            "title": f"models.dev lists {m.get('name') or mid} for {provider}",
                            "url": f"https://models.dev/?search={mid}",
                            "text": f"Release date on models.dev: {m.get('release_date', '?')}."})
    elif kind == "rss":
        for it in news_sources.fetch({"name": signal["name"], "type": "rss", "url": signal["url"], "tier": "leak"}, {}):
            for key, c in companies.items():
                ids = extract_models(f"{it['title']} {it['summary']}", c["_patterns"])
                if ids:
                    out.append({"uid": it["id"], "company": key, "model_ids": ids[:3], "title": it["title"],
                                "url": it["url"], "text": it["summary"][:400]})
    return out
