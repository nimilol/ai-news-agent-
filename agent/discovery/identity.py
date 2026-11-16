"""Stable identities for discovered resources, so the same thing is never recommended twice.

    GitHub        github:<repository node id>  (survives renames and transfers; the URL alone doesn't)
    Hugging Face  hf:<model|space|dataset>:<org>/<name>
    GitLab        gitlab:<project id> · Codeberg codeberg:<repository id>
    MCP registry  the GitHub identity of its source repository when it has one, else mcp:<server name>

Every resource also keeps alias keys (canonical URL, lower-cased owner/name) so a resource met again through
another search, another platform listing or a URL variant (www, .git, trailing slash, /tree/main, ?tab=...) is
recognised as the same one.
"""

import re
from urllib.parse import urlsplit

_GH = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)


def canonical_url(url):
    """https, lower-case host without www, no query/fragment/.git/trailing slash; GitHub and GitLab URLs are cut
    back to the repository itself (…/owner/repo/tree/main/x → …/owner/repo) and lower-cased."""
    if not url:
        return ""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    p = urlsplit(url)
    host = p.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    path = re.sub(r"\.git$", "", p.path.rstrip("/"))
    parts = [x for x in path.split("/") if x]
    if host in ("github.com", "codeberg.org") and len(parts) >= 2:
        parts = parts[:2]
        path = "/" + "/".join(parts).lower()
    elif host == "gitlab.com" and parts:
        cut = parts.index("-") if "-" in parts else len(parts)
        path = "/" + "/".join(parts[:cut]).lower()
    elif host == "huggingface.co" and parts:
        keep = 3 if parts[0] in ("spaces", "datasets") else 2
        path = "/" + "/".join(parts[:keep])
    return f"https://{host}{path}"


def github_full_name(url):
    """'owner/repo' for any GitHub URL (or None)."""
    m = _GH.match((url or "").strip())
    if not m:
        return None
    repo = re.sub(r"\.git$", "", m.group(2))
    return f"{m.group(1)}/{repo}".lower()


def alias_keys(candidate):
    keys = [f"url:{canonical_url(candidate.get('url'))}"]
    full = github_full_name(candidate.get("url")) or (candidate.get("platform") == "github" and candidate.get("name"))
    if full:
        keys.append(f"gh:{full.lower()}")
    for extra in candidate.get("source_urls") or []:
        keys.append(f"url:{canonical_url(extra)}")
        if github_full_name(extra):
            keys.append(f"gh:{github_full_name(extra)}")
    return list(dict.fromkeys(k for k in keys if k and not k.endswith(":")))


def resource_id(candidate):
    platform = candidate["platform"]
    if platform == "github" and candidate.get("node_id"):
        return f"github:{candidate['node_id']}"
    if platform == "github":
        return f"github:{(candidate.get('name') or '').lower()}"
    if platform == "huggingface":
        return f"hf:{candidate['kind']}:{candidate['name'].lower()}"
    if platform in ("gitlab", "codeberg") and candidate.get("platform_id") is not None:
        return f"{platform}:{candidate['platform_id']}"
    return f"{platform}:{canonical_url(candidate.get('url')) or candidate.get('name', '').lower()}"
