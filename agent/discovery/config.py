"""Loads and checks discovery.yaml (regular expressions compiled once)."""

import re

from ..common import load_yaml

SECTIONS = ["agent_building", "skills", "prompting", "app_development", "automation", "hackathon", "creator_tools",
            "productivity"]


def load(path="discovery.yaml", data=None):
    cfg = data if data is not None else load_yaml(path)
    for cat in cfg.get("categories", {}).values():
        cat["_patterns"] = [re.compile(k, re.I) for k in cat.get("keywords", [])]
    for p in cfg.get("profile", []):
        p["_patterns"] = [re.compile(k, re.I) for k in p.get("keywords", [])]
    cfg.setdefault("settings", {})
    cfg["settings"]["_exclude"] = [re.compile(k, re.I) for k in cfg["settings"].get("exclude", [])]
    return cfg


def validate(cfg):
    errors = []
    missing = [k for k in SECTIONS if k not in cfg.get("categories", {})]
    if missing:
        errors.append(f"categories missing: {missing}")
    for key, cat in cfg.get("categories", {}).items():
        for k in cat.get("keywords", []):
            try:
                re.compile(k, re.I)
            except re.error as e:
                errors.append(f"category {key}: bad keyword {k!r} ({e})")
    prios = cfg.get("priorities", {})
    if sorted(prios) != ["P0", "P1", "P2", "P3"] or not (prios["P0"] > prios["P1"] > prios["P2"] > prios["P3"]):
        errors.append("priorities must define P0 > P1 > P2 > P3 score thresholds")
    for q in cfg.get("sources", {}).get("github", {}).get("queries", []):
        if q.get("mode") not in ("new", "active") or not q.get("q"):
            errors.append(f"github query {q}: needs q and mode new|active")
    return errors
