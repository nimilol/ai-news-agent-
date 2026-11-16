"""Independent benchmark results for the weekly model report.

Configured in releases.yaml → independent_benchmarks. Today one source qualifies (checked 2026-11-16):

  LMArena  the official lmarena-ai/leaderboard-dataset on Hugging Face (updated daily), read through Hugging Face's
           public datasets API. Method: crowdsourced, blind, pairwise human votes between two anonymous models,
           turned into a Bradley-Terry ("Elo-style") rating with a 95% confidence interval, per category.

Rejected: Aider's polyglot leaderboard (no entries since Oct 2025); lmarena.ai, epoch.ai, swebench.com, livebench.ai
and artificialanalysis.ai (no machine-readable feed without a key, or unreachable).

Rules the report follows: an LMArena rating is only ever compared with another LMArena rating in the same category
and leaderboard; it is never put side by side with a company-reported score from a different evaluation; and when
the two confidence intervals overlap, the difference is reported as not significant.
"""

import re
import time

from ..common import get
from . import identity

ROWS_API = "https://datasets-server.huggingface.co/rows"
PAGE = 100


def _rows(dataset, config, offset, length, tries=3):
    params = {"dataset": dataset, "config": config, "split": "latest", "offset": offset, "length": length}
    for attempt in range(tries):
        try:
            return get(ROWS_API, params=params, timeout=40).json()
        except Exception:  # noqa: BLE001 - the API is occasionally slow: try again, then give up on this board
            if attempt + 1 == tries:
                raise
            time.sleep(3 * (attempt + 1))


def _order(category):
    """The dataset lists 'overall' first, then the other categories alphabetically."""
    return "" if category == "overall" else category


def read_category(dataset, config, category, max_rows=700):
    """Every row of one leaderboard category (rows are grouped by category and sorted by rank)."""
    first = _rows(dataset, config, 0, 1)
    total = first.get("num_rows_total", 0)
    if not total:
        return []
    lo, hi = 0, total  # binary search for the first row whose category is >= the one we want
    while lo < hi:
        mid = (lo + hi) // 2
        got = _rows(dataset, config, mid, 1)["rows"]
        if got and _order(got[0]["row"]["category"]) < _order(category):
            lo = mid + 1
        else:
            hi = mid
    out, offset = [], lo
    while offset < total and len(out) < max_rows:
        page = [r["row"] for r in _rows(dataset, config, offset, PAGE)["rows"]]
        mine = [r for r in page if r.get("category") == category]
        out += mine
        if len(mine) < len(page) or not page:
            break
        offset += PAGE
    return out


def label(bench, source, board):
    """'text/coding' → 'Coding' (the label from releases.yaml)."""
    return (bench.get(source) or {}).get("labels", {}).get(board, board)


def fetch_lmarena(source):
    """{leaderboard label: [rows]} for the configured categories, e.g. {"text/overall": [...]}."""
    out = {}
    for board in source.get("boards", []):
        config, category = board["config"], board["category"]
        try:
            out[f"{config}/{category}"] = read_category(source["dataset"], config, category)
        except Exception as e:  # noqa: BLE001 - benchmarks are a bonus; the report goes out without them
            print(f"    LMArena {config}/{category} skipped: {str(e)[:120]}")
    return out


FETCHERS = {"lmarena_hf": fetch_lmarena}


def fetch_all(cfg):
    """{source name: {"source": cfg, "boards": {label: rows}}} for every configured independent benchmark."""
    out = {}
    for src in cfg.get("independent_benchmarks", []):
        fetcher = FETCHERS.get(src.get("type"))
        if fetcher:
            boards = fetcher(src)
            if any(boards.values()):
                labels = {f"{b['config']}/{b['category']}": b.get("label") or b["category"] for b in src["boards"]}
                out[src["name"]] = {"source": src, "boards": boards, "labels": labels}
    return out


def _clean_name(name):
    """'Claude Opus 5.5 (High)' → ('Claude Opus 5.5', 'High')."""
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", name or "")
    return (m.group(1), m.group(2)) if m else (name or "", "")


def best_match(rows, company_cfg, base):
    """The highest-rated row whose model is the model with this base key (any reasoning-effort variant)."""
    hits = []
    for r in rows:
        name, variant = _clean_name(r.get("model_name", ""))
        if identity.base_key(name, company_cfg) == base:
            hits.append({**r, "name": name, "variant": variant})
    return max(hits, key=lambda r: r.get("rating") or 0) if hits else None


def significant(a, b):
    """Is a's rating clearly above b's? Only when a's lower bound is above b's upper bound."""
    return (a.get("rating_lower") or 0) > (b.get("rating_upper") or 0)


def describe(row):
    variant = f" ({row['variant']})" if row.get("variant") else ""
    return (f"{row['name']}{variant}: {row['rating']:.0f} "
            f"(95% CI {row['rating_lower']:.0f}-{row['rating_upper']:.0f}, rank {row['rank']:.0f}, "
            f"{row['vote_count']:,.0f} votes)")


def compare(new, prev):
    """A plain-language comparison of two rows from the SAME leaderboard and category."""
    if not new or not prev:
        return None
    diff = new["rating"] - prev["rating"]
    if significant(new, prev):
        verdict = f"higher by {diff:.0f} points, outside both confidence intervals"
    elif significant(prev, new):
        verdict = f"lower by {-diff:.0f} points, outside both confidence intervals"
    else:
        verdict = f"{diff:+.0f} points, within the confidence intervals (not a clear difference)"
    return verdict


def lookup(bench, company_cfg, base):
    """{source name: {board label: row}} for one model."""
    out = {}
    for name, data in bench.items():
        rows = {label: best_match(rows, company_cfg, base) for label, rows in data["boards"].items()}
        rows = {k: v for k, v in rows.items() if v}
        if rows:
            out[name] = rows
    return out


def leaders(bench, companies, board="text/overall", per_company=1):
    """The best-rated model of each monitored company on one board (for the landscape snapshot)."""
    out = []
    for name, data in bench.items():
        rows = data["boards"].get(board) or []
        for key, c in companies.items():
            mine = [r for r in rows if identity.extract_models(_clean_name(r.get("model_name", ""))[0], c["_patterns"])]
            for r in sorted(mine, key=lambda r: -(r.get("rating") or 0))[:per_company]:
                n, v = _clean_name(r["model_name"])
                out.append({"company": key, "source": name, **r, "name": n, "variant": v})
    return sorted(out, key=lambda r: -(r.get("rating") or 0))
