"""Where discoveries come from. Every fetcher returns *candidates*: plain dicts with the same fields, whatever the
platform. Nothing here downloads or runs code; READMEs are fetched as text only, to be read as untrusted data.

    GitHub         rotated repository searches (by topic, tool, framework, problem; new and active), new
                   repositories of official AI organisations, and GitHub Trending (AI repositories)
    Hugging Face   trending and searched Spaces, trending models and datasets
    MCP registry   registry.modelcontextprotocol.io (the official MCP server registry), recently updated servers
    GitLab         project search (public API), at most daily
    Codeberg       repository search (Gitea API), at most daily; currently often throttled by Codeberg
"""

import re
import time
from datetime import timedelta

import requests

from ..common import clean_text, env, get, now_utc, parse_date, session
from . import identity

GITHUB_API = "https://api.github.com"


class RateLimited(Exception):
    pass


class GitHub:
    """A small GitHub API client with a per-run request budget, rate-limit handling and backoff."""

    def __init__(self, budget=120):
        self.budget = budget
        self.used = 0
        self.disabled = False

    def _headers(self):
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if env("GITHUB_TOKEN"):
            h["Authorization"] = f"Bearer {env('GITHUB_TOKEN')}"
        return h

    def get(self, path, params=None, tries=3):
        if self.disabled or self.used >= self.budget:
            raise RateLimited("GitHub request budget for this run is used up")
        for attempt in range(tries):
            self.used += 1
            try:
                r = session().get(GITHUB_API + path, params=params, headers=self._headers(), timeout=25)
            except requests.RequestException:
                if attempt + 1 == tries:
                    raise
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 200:
                return r.json()
            remaining = r.headers.get("x-ratelimit-remaining")
            if r.status_code in (403, 429) and (remaining == "0" or "rate limit" in r.text.lower()):
                wait = int(r.headers.get("retry-after") or 0)
                reset = int(r.headers.get("x-ratelimit-reset") or 0) - int(time.time())
                wait = wait or (reset if 0 < reset <= 60 else 0)
                if wait and attempt + 1 < tries:
                    time.sleep(min(wait, 60))
                    continue
                self.disabled = True  # don't burn the rest of the quota; other sources still run
                raise RateLimited(f"GitHub rate limit ({r.status_code})")
            if r.status_code >= 500 and attempt + 1 < tries:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
        raise RateLimited("GitHub did not answer")

    def search(self, q, sort="stars", per_page=20):
        return self.get("/search/repositories", {"q": q, "sort": sort, "order": "desc", "per_page": per_page}).get("items", [])


def from_github_repo(r, found_by):
    return {
        "platform": "github", "kind": "repository", "node_id": r.get("node_id"), "platform_id": r.get("id"),
        "name": r.get("full_name"), "display": r.get("name"), "owner": (r.get("owner") or {}).get("login"),
        "owner_type": (r.get("owner") or {}).get("type"), "url": r.get("html_url"),
        "description": clean_text(r.get("description") or "", 400), "topics": r.get("topics") or [],
        "language": r.get("language"), "stars": r.get("stargazers_count", 0), "forks": r.get("forks_count", 0),
        "open_issues": r.get("open_issues_count", 0), "pushed_at": r.get("pushed_at"), "created_at": r.get("created_at"),
        "license_spdx": ((r.get("license") or {}).get("spdx_id")), "license_name": (r.get("license") or {}).get("name"),
        "archived": bool(r.get("archived")), "fork": bool(r.get("fork")), "default_branch": r.get("default_branch") or "main",
        "homepage": r.get("homepage"), "found_by": [found_by],
    }


def _since(days):
    return (now_utc() - timedelta(days=days)).date().isoformat()


def github_query(q):
    """The full search for one configured query: 'new' = created in the last 60 days, 'active' = pushed in the last
    14 days, each with its minimum stars."""
    base = f"{q['q']} stars:>={q.get('min_stars', 20)} archived:false"
    if q.get("mode") == "active":
        return f"{base} pushed:>={_since(14)}", "updated"
    return f"{base} created:>={_since(60)}", "stars"


def next_queries(queries, searches, per_run, min_hours):
    """The next searches to run: those not run for `min_hours`, least recently run first."""
    due = []
    for q in queries:
        key = f"github:{q['q']}"
        last = parse_date((searches.get(key) or {}).get("last_run"))
        if last and (now_utc() - last).total_seconds() < min_hours * 3600:
            continue
        due.append((last.isoformat() if last else "", key, q))
    return [(key, q) for _, key, q in sorted(due, key=lambda x: x[0])[:per_run]]


def github_search(gh, queries, searches, per_run=8, min_hours=20):
    out = []
    for key, q in next_queries(queries, searches, per_run, min_hours):
        query, sort = github_query(q)
        try:
            items = gh.search(query, sort=sort)
        except RateLimited:
            break
        searches[key] = {"last_run": now_utc().replace(microsecond=0).isoformat(), "results": len(items)}
        out += [from_github_repo(r, f"search: {q['q']}") for r in items]
    return out


def github_orgs(gh, orgs, days=21):
    """Repositories official AI organisations created recently."""
    out, cutoff = [], now_utc() - timedelta(days=days)
    for org in orgs:
        try:
            repos = gh.get(f"/orgs/{org}/repos", {"sort": "created", "direction": "desc", "per_page": 10})
        except RateLimited:
            break
        except Exception as e:  # noqa: BLE001
            print(f"    skipped org {org}: {str(e)[:80]}")
            continue
        for r in repos:
            created = parse_date(r.get("created_at"))
            if created and created >= cutoff and not r.get("fork") and not r.get("archived"):
                c = from_github_repo(r, f"new in official org {org}")
                c["official_org"] = True
                out.append(c)
    return out


def github_trending():
    from ..builders import trending_repos

    out = []
    for t in trending_repos(ai_only=True):
        out.append({"platform": "github", "kind": "repository", "name": t["name"], "url": t["url"],
                    "owner": t["name"].split("/")[0], "description": t.get("text", ""), "found_by": ["GitHub Trending"],
                    "needs_enrichment": True})
    return out


def enrich_github(gh, candidates, batch=5):
    """Fill in GitHub metadata (id, stars, licence, last push, …) for candidates known only by URL (trending repos,
    MCP servers), several repositories per search request."""
    todo = [c for c in candidates if c.get("needs_enrichment") and identity.github_full_name(c["url"])]
    by_name = {}
    for i in range(0, len(todo), batch):
        names = [identity.github_full_name(c["url"]) for c in todo[i:i + batch]]
        try:
            items = gh.search(" ".join(f"repo:{n}" for n in names), per_page=batch)
        except RateLimited:
            break
        except Exception as e:  # noqa: BLE001
            print(f"    enrichment skipped: {str(e)[:80]}")
            continue
        for r in items:
            by_name[r["full_name"].lower()] = r
    out = []
    for c in candidates:
        if not c.get("needs_enrichment"):
            out.append(c)
            continue
        r = by_name.get(identity.github_full_name(c["url"]) or "")
        if r:
            full = from_github_repo(r, c["found_by"][0])
            full["found_by"] = c["found_by"]
            full["source_urls"] = c.get("source_urls", [])
            full["mcp"] = c.get("mcp")
            out.append(full)
    return out


def readme(c, chars=12000):
    """README text (untrusted). GitHub via raw.githubusercontent.com (no API quota); Hugging Face via /raw/main."""
    try:
        if c["platform"] == "github" and c.get("name"):
            for name in ("README.md", "readme.md", "README.rst", "README"):
                r = session().get(f"https://raw.githubusercontent.com/{c['name']}/{c.get('default_branch') or 'main'}/{name}",
                                  timeout=20)
                if r.status_code == 200:
                    return r.text[:chars]
        elif c["platform"] == "huggingface":
            prefix = {"space": "spaces/", "dataset": "datasets/"}.get(c["kind"], "")
            r = session().get(f"https://huggingface.co/{prefix}{c['name']}/raw/main/README.md", timeout=20)
            if r.status_code == 200:
                return r.text[:chars]
        elif c["platform"] == "gitlab" and c.get("readme_url"):
            r = session().get(c["readme_url"].replace("/-/blob/", "/-/raw/"), timeout=20)
            if r.status_code == 200:
                return r.text[:chars]
    except requests.RequestException:
        pass
    return ""


def github_latest_release(gh, full_name):
    """{tag, name, body, published_at, url} or None (no releases, or not reachable)."""
    try:
        r = gh.get(f"/repos/{full_name}/releases/latest")
    except Exception:  # noqa: BLE001 - 404 = no releases; anything else: try again another day
        return None
    return {"tag": r.get("tag_name"), "name": r.get("name") or "", "body": (r.get("body") or "")[:3000],
            "published_at": r.get("published_at"), "url": r.get("html_url")}


def github_contributors(gh, full_name):
    """Number of contributors (from the pagination of a 1-per-page listing), or None."""
    try:
        r = session().get(f"{GITHUB_API}/repos/{full_name}/contributors", params={"per_page": 1, "anon": 1},
                          headers=gh._headers(), timeout=20)
        gh.used += 1
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    m = re.search(r'page=(\d+)>; rel="last"', r.headers.get("link", ""))
    return int(m.group(1)) if m else len(r.json())


# ----------------------------------------------------------------------------------------------- Hugging Face

def _hf_license(item):
    tags = item.get("tags") or []
    lic = next((t.split(":", 1)[1] for t in tags if t.startswith("license:")), None)
    return lic or (item.get("cardData") or {}).get("license")


def huggingface(cfg):
    out = []

    def add(kind, items, found_by):
        for m in items:
            mid = m.get("id") or m.get("modelId")
            if not mid:
                continue
            prefix = {"space": "spaces/", "dataset": "datasets/"}.get(kind, "")
            out.append({"platform": "huggingface", "kind": kind, "name": mid, "display": mid.split("/")[-1],
                        "owner": mid.split("/")[0], "url": f"https://huggingface.co/{prefix}{mid}",
                        "description": clean_text((m.get("cardData") or {}).get("short_description")
                                                  or (m.get("cardData") or {}).get("title") or m.get("description") or "", 300),
                        "topics": [t for t in (m.get("tags") or []) if ":" not in t][:12],
                        "likes": m.get("likes", 0), "downloads": m.get("downloads", 0),
                        "pushed_at": m.get("lastModified") or m.get("createdAt"), "created_at": m.get("createdAt"),
                        "license_spdx": _hf_license(m), "gated": bool(m.get("gated")), "sdk": m.get("sdk"),
                        "pipeline_tag": m.get("pipeline_tag"), "found_by": [found_by]})
    if cfg.get("spaces_trending"):
        add("space", get("https://huggingface.co/api/spaces", params={"sort": "trendingScore", "limit": cfg["spaces_trending"],
                                                                      "full": "true"}).json(), "Hugging Face trending Spaces")
    for term in cfg.get("spaces_search") or []:
        add("space", get("https://huggingface.co/api/spaces", params={"search": term, "sort": "likes", "limit": 10,
                                                                      "full": "true"}).json(), f"Hugging Face Spaces: {term}")
    if cfg.get("models_trending"):
        add("model", get("https://huggingface.co/api/models", params={"sort": "trendingScore", "limit": cfg["models_trending"],
                                                                      "full": "true"}).json(), "Hugging Face trending models")
    if cfg.get("datasets_trending"):
        add("dataset", get("https://huggingface.co/api/datasets", params={"sort": "trendingScore",
                                                                          "limit": cfg["datasets_trending"]}).json(),
            "Hugging Face trending datasets")
    return out


# ----------------------------------------------------------------------------------------------- other platforms

def mcp_registry(cfg, since_days=7):
    """Recently updated servers in the official MCP registry. Servers with a GitHub repository are evaluated as that
    repository (so they're never recommended twice); hosted-only servers are skipped (no code or licence to check)."""
    data = get(cfg["url"], params={"limit": cfg.get("limit", 100),
                                   "updated_since": (now_utc() - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")}).json()
    out, seen = [], set()
    for entry in data.get("servers") or []:
        s = entry.get("server") or {}
        meta = (entry.get("_meta") or {}).get("io.modelcontextprotocol.registry/official") or {}
        repo = (s.get("repository") or {}).get("url")
        full = identity.github_full_name(repo)
        if not full or not meta.get("isLatest", True) or full in seen or full == "modelcontextprotocol/registry":
            continue
        seen.add(full)
        out.append({"platform": "github", "kind": "repository", "name": full, "url": f"https://github.com/{full}",
                    "description": clean_text(s.get("description") or "", 300), "found_by": ["MCP registry"],
                    "needs_enrichment": True, "source_urls": [repo],
                    "mcp": {"name": s.get("name"), "version": s.get("version")}})
    return out


def gitlab(cfg):
    out = []
    after = (now_utc() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for q in cfg.get("queries") or []:
        for p in get("https://gitlab.com/api/v4/projects", params={"search": q, "order_by": "star_count", "per_page": 20,
                                                                   "last_activity_after": after}).json():
            if p.get("star_count", 0) < cfg.get("min_stars", 10):
                continue
            out.append({"platform": "gitlab", "kind": "repository", "platform_id": p["id"], "name": p["path_with_namespace"],
                        "display": p.get("name"), "owner": (p.get("namespace") or {}).get("full_path"), "url": p["web_url"],
                        "description": clean_text(p.get("description") or "", 400), "topics": p.get("topics") or [],
                        "stars": p.get("star_count", 0), "forks": p.get("forks_count", 0),
                        "pushed_at": p.get("last_activity_at"), "created_at": p.get("created_at"),
                        "readme_url": p.get("readme_url"), "archived": bool(p.get("archived")),
                        "found_by": [f"GitLab: {q}"], "needs_license": True})
    return out


def gitlab_license(c):
    try:
        lic = get(f"https://gitlab.com/api/v4/projects/{c['platform_id']}", params={"license": "true"}).json().get("license")
    except Exception:  # noqa: BLE001
        return None
    return (lic or {}).get("key", "").upper() or None


def codeberg(cfg):
    out = []
    for q in cfg.get("queries") or []:
        data = get("https://codeberg.org/api/v1/repos/search", params={"q": q, "sort": "stars", "order": "desc",
                                                                       "limit": 20}).json()
        for r in data.get("data") or []:
            if r.get("stars_count", 0) < cfg.get("min_stars", 5) or r.get("archived"):
                continue
            lic = (r.get("licenses") or [None])[0]
            out.append({"platform": "codeberg", "kind": "repository", "platform_id": r["id"], "name": r["full_name"],
                        "display": r.get("name"), "owner": (r.get("owner") or {}).get("login"), "url": r["html_url"],
                        "description": clean_text(r.get("description") or "", 400), "topics": r.get("topics") or [],
                        "stars": r.get("stars_count", 0), "forks": r.get("forks_count", 0),
                        "pushed_at": r.get("updated_at"), "created_at": r.get("created_at"),
                        "license_spdx": lic, "found_by": [f"Codeberg: {q}"]})
    return out
