"""modelaccess.yaml, with defaults so a partial file still works."""

import re

from ..common import load_yaml

DEFAULTS = {"low_cost_max_blended": 1.0, "heavy_usage_ratio": 3, "min_context_for_agents": 64000,
            "openrouter_endpoint_lookups": 25, "stale_hours": 12, "max_alerts_per_run": 6, "alert_cooldown_days": 30,
            "history_keep": 3000, "leads_keep_days": 45, "weekly_top": 8,
            "exclude_patterns": ["uncensored", "abliterated", "heretic", "jailbreak", "nsfw"]}


def load(name="modelaccess.yaml"):
    cfg = load_yaml(name) or {}
    cfg["settings"] = {**DEFAULTS, **(cfg.get("settings") or {})}
    cfg.setdefault("companies", {})
    cfg.setdefault("providers", {})
    cfg["providers"].setdefault("official", {})
    cfg["providers"].setdefault("free_tier", {})
    cfg["providers"].setdefault("subscription_patterns", [])
    cfg["providers"].setdefault("vetted", [])
    cfg["providers"].setdefault("blocked", [])
    cfg.setdefault("platforms", [])
    cfg.setdefault("catalogs", {})
    cfg.setdefault("leads", {})
    return cfg


def alias_map(cfg):
    """author id as the catalogs spell it -> company key."""
    out = {}
    for key, c in cfg["companies"].items():
        out[key] = key
        for a in (c or {}).get("aliases") or []:
            out[str(a).lower()] = key
    return out


def excluded(text, cfg):
    t = str(text or "").lower()
    return any(re.search(p, t) for p in cfg["settings"]["exclude_patterns"])
