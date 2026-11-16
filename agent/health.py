"""Agent health for the /health command and the ⚙️ section: workflow runs, failing sources, Telegram delivery.

Workflow runs come live from the GitHub API (GITHUB_TOKEN and GITHUB_REPOSITORY are set inside GitHub Actions), so
nothing extra has to be saved or committed. Everything else is read from the state files."""

import json
from datetime import timedelta

import requests

from . import messages as msg
from .common import env, now_utc, parse_date

FAILING_AFTER = 3


def workflow_runs(limit=60):
    """{workflow name: [runs newest first]} from the GitHub API, or None when it can't be reached."""
    repo, token = env("GITHUB_REPOSITORY"), env("GITHUB_TOKEN")
    if not repo or not token:
        return None
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/actions/runs", params={"per_page": limit},
                         headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                         timeout=20)
        r.raise_for_status()
    except requests.RequestException:
        return None
    out = {}
    for run in r.json().get("workflow_runs", []):
        out.setdefault(run.get("name", "?"), []).append(run)
    return out


def _ago(iso):
    d = parse_date(iso)
    if not d:
        return "?"
    hours = (now_utc() - d).total_seconds() / 3600
    return f"{int(hours * 60)} min ago" if hours < 1 else f"{hours:.0f} h ago" if hours < 48 else f"{hours / 24:.0f} days ago"


def failing_sources(root=None):
    """[(run, source, failed runs in a row)] for sources failing FAILING_AFTER+ runs in a row."""
    from . import state

    out = []
    for name in ("digest", "alerts", "releases"):
        path = (root or state.ROOT) / "state" / f"{name}.json"
        if not path.exists():
            continue
        failures = json.loads(path.read_text(encoding="utf-8")).get("failures", {})
        out += [(name, src, n) for src, n in sorted(failures.items()) if n >= FAILING_AFTER]
    return out


def outbox_summary():
    from .releases.store import Store

    counts = {}
    for n in Store().outbox.values():
        counts[n["status"]] = counts.get(n["status"], 0) + 1
    return counts


def render(runs=None):
    """The /health message."""
    lines = [msg.section_heading("health"), f"<i>{now_utc():%a %d %b, %H:%M} UTC</i>"]
    runs = workflow_runs() if runs is None else runs
    if runs:
        lines.append("<b>Workflow runs</b>")
        for name in sorted(runs):
            last = runs[name][0]
            ok = {"success": "✅", "failure": "❌", "cancelled": "⏹", None: "⏳"}.get(last.get("conclusion"), "⚠️")
            lines.append(f"{ok} {msg.link(name, last.get('html_url'))} · {_ago(last.get('created_at'))}")
        alerts = [parse_date(r.get("created_at")) for r in runs.get("Instant alerts", [])[:6]]
        alerts = [d for d in alerts if d]
        if len(alerts) >= 3:
            gaps = [(a - b) / timedelta(hours=1) for a, b in zip(alerts, alerts[1:])]
            lines.append(f"<i>Instant alerts (scheduled hourly) actually ran every {min(gaps):.0f}-{max(gaps):.0f} h "
                         "lately; GitHub skips scheduled runs when busy.</i>")
    else:
        lines.append("<i>Workflow runs: not available here (needs GitHub Actions or the instant bot's token).</i>")
    failing = failing_sources()
    lines.append("<b>Sources</b>")
    if failing:
        lines += [f"⚠️ {msg.e(src)} ({run}): failed {n} runs in a row" for run, src, n in failing]
    else:
        lines.append("✅ every source answered on its last run")
    box = outbox_summary()
    pending = box.get("PENDING", 0) + box.get("RETRY_PENDING", 0)
    lines.append("<b>Release alert delivery</b>")
    lines.append(f"{'✅' if not pending and not box.get('FAILED') else '⚠️'} {box.get('SENT', 0)} confirmed by Telegram"
                 f" · {pending} waiting · {box.get('FAILED', 0)} failed")
    return msg.paginate([("\n".join(lines), "health")])
