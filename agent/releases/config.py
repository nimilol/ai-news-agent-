"""Loads and checks releases.yaml. A source only counts as official when its address is on one of the company's
own hosts (or it is the company's own Hugging Face / GitHub organisation)."""

import re
from urllib.parse import urlsplit

from ..common import load_yaml
from .parsers import CATALOG_PARSERS, CHANGELOG_PARSERS

ROLES = {"announcement", "changelog", "catalog", "repo"}
TYPES = {"rss", "page_links", "markdown", "html", "json_api", "huggingface_org", "github_org"}
EARLY_TYPES = {"openrouter", "models_dev", "rss"}
CATEGORIES = ["NEW_MODEL_RELEASE", "MAJOR_MODEL_UPGRADE", "MODEL_AVAILABILITY", "DOCUMENTATION_OR_PRICING_CHANGE",
              "PRODUCT_OR_FEATURE_ANNOUNCEMENT", "UNCONFIRMED_EARLY_SIGNAL"]
REPO_HOSTS = {"huggingface_org": "huggingface.co", "github_org": "github.com"}

DEFAULT_SETTINGS = {
    "notify": {"NEW_MODEL_RELEASE": True, "MAJOR_MODEL_UPGRADE": True, "MODEL_AVAILABILITY": True,
               "DOCUMENTATION_OR_PRICING_CHANGE": False, "PRODUCT_OR_FEATURE_ANNOUNCEMENT": False,
               "UNCONFIRMED_EARLY_SIGNAL": False, "REQUIRES_MANUAL_REVIEW": True},
    "receipts_for": ["NEW_MODEL_RELEASE", "MAJOR_MODEL_UPGRADE"],
    "max_delivery_attempts": 6,
    "failing_alert_after": 3,
    "max_llm_calls": 4,
    "article_chars": 12000,
    "max_notifications_per_run": 8,
}


def host_of(url):
    host = urlsplit(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_official_url(url, company_cfg):
    """True when `url` is on one of the company's own hosts (or a subdomain of one)."""
    host = host_of(url)
    return bool(host) and any(host == h or host.endswith("." + h) for h in company_cfg.get("official_hosts", []))


def compile_patterns(company_cfg):
    return [re.compile(p, re.I) for p in company_cfg.get("model_patterns", [])]


def load(path="releases.yaml", data=None):
    """Returns the release config with defaults filled in and each company's patterns compiled (`_patterns`)."""
    cfg = data if data is not None else load_yaml(path)
    settings = {**DEFAULT_SETTINGS, **(cfg.get("settings") or {})}
    settings["notify"] = {**DEFAULT_SETTINGS["notify"], **((cfg.get("settings") or {}).get("notify") or {})}
    companies = {}
    for key, c in (cfg.get("companies") or {}).items():
        c = dict(c)
        c["key"] = key
        c["_patterns"] = compile_patterns(c)
        c["sources"] = [dict(s, company=key) for s in c.get("sources") or []]
        companies[key] = c
    return {"settings": settings, "companies": companies, "early_signals": list(cfg.get("early_signals") or []),
            "independent_benchmarks": list(cfg.get("independent_benchmarks") or [])}


def validate(cfg):
    """Every problem with the config, as readable strings (empty list = valid)."""
    errors, names = [], set()
    if not cfg["companies"]:
        errors.append("no companies configured")
    for key, c in cfg["companies"].items():
        for field in ("label", "product", "official_hosts", "model_patterns", "sources"):
            if not c.get(field):
                errors.append(f"{key}: missing '{field}'")
        for p in c.get("model_patterns", []):
            try:
                re.compile(p, re.I)
            except re.error as e:
                errors.append(f"{key}: bad model pattern {p!r} ({e})")
        roles = set()
        for s in c.get("sources", []):
            label = f"{key} / {s.get('name', '?')}"
            if not s.get("name"):
                errors.append(f"{label}: missing name")
            elif s["name"] in names:
                errors.append(f"{label}: duplicate source name")
            names.add(s.get("name"))
            if s.get("role") not in ROLES:
                errors.append(f"{label}: role must be one of {sorted(ROLES)}")
            roles.add(s.get("role"))
            if s.get("type") not in TYPES:
                errors.append(f"{label}: type must be one of {sorted(TYPES)}")
                continue
            if s["type"] in REPO_HOSTS:
                if s.get("role") != "repo" or not s.get("org"):
                    errors.append(f"{label}: {s['type']} needs role 'repo' and the company's 'org'")
                continue
            if not s.get("url"):
                errors.append(f"{label}: missing url")
            elif not is_official_url(s["url"], c):
                errors.append(f"{label}: {host_of(s['url'])} is not one of {key}'s official hosts")
            if s["type"] in ("markdown", "html", "json_api") or s.get("parser"):
                parser = s.get("parser")
                known = CATALOG_PARSERS if s.get("role") == "catalog" else CHANGELOG_PARSERS
                if parser not in known:
                    errors.append(f"{label}: unknown parser {parser!r} for role {s.get('role')}")
            if s["type"] == "page_links":
                for field in ("pattern", "exclude_url"):
                    if s.get(field):
                        try:
                            re.compile(s[field])
                        except re.error as e:
                            errors.append(f"{label}: bad {field} ({e})")
                if not s.get("pattern"):
                    errors.append(f"{label}: page_links needs a link 'pattern'")
        if "announcement" not in roles and "repo" not in roles:
            errors.append(f"{key}: needs at least one announcement or repo source")
        if "catalog" not in roles and "changelog" not in roles:
            errors.append(f"{key}: needs at least one catalog or changelog source")
    for s in cfg["early_signals"]:
        if s.get("type") not in EARLY_TYPES:
            errors.append(f"early signal {s.get('name')}: type must be one of {sorted(EARLY_TYPES)}")
        if s.get("name") in names:
            errors.append(f"early signal {s.get('name')}: duplicate source name")
        names.add(s.get("name"))
    for b in cfg.get("independent_benchmarks", []):
        if b.get("type") != "lmarena_hf" or not b.get("boards") or not b.get("dataset"):
            errors.append(f"independent benchmark {b.get('name')}: needs type lmarena_hf, a dataset and boards")
        if not b.get("methodology"):
            errors.append(f"independent benchmark {b.get('name')}: describe its methodology")
    for cat in cfg["settings"]["notify"]:
        if cat not in CATEGORIES + ["REQUIRES_MANUAL_REVIEW"]:
            errors.append(f"settings.notify: unknown category {cat}")
    return errors
