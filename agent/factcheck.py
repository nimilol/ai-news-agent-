"""Fact-check pass: a second AI call compares every story with the items it cites and fixes or drops
anything the sources don't support. It runs on a different provider than the writer when possible,
and it never blocks the brief: if it fails, the brief goes out marked "not fact-checked"."""

import json
import re

from . import llm
from .accuracy import enforce_status, publisher
from .common import clean_text

VERIFY_PROMPT = """You are a strict fact-checker for an AI news brief.
Each numbered story below lists the source items it is based on. Check the story's title and summary
against those sources only.

Answer with JSON only:
{"checks": [{"story": 1, "verdict": "ok | fix | drop", "title": "", "summary": "", "remove_items": []}]}

- "ok": every claim is supported by the sources. Leave title and summary empty.
- "fix": something is wrong or overstated (a number, name, date, a rumor written as fact, two unrelated
  events merged). Give the corrected title and summary using only facts in the sources, and list in
  "remove_items" any item numbers that are about a different event than the story.
- "drop": the sources do not support the story at all.
Be strict with numbers, names and dates, and with "launched" versus "reported" or "rumored".
Include one check for every story."""


def _build(brief, items, text_chars):
    lines = []
    for i, s in enumerate(brief["stories"], 1):
        lines.append(f"Story {i}: {s['title']}\nSummary: {s['summary']}\nSources:")
        for n in s["items"]:
            it = items[n - 1]
            body = it.get("article") or it["summary"]
            lines.append(f"  [item {n}] ({it['tier']} · {publisher(it)}) {it['title']} :: {clean_text(body, text_chars)}")
        lines.append("")
    return "\n".join(lines)


def _parse(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1]).get("checks") or []


def factcheck(config, brief, items, writer):
    """Returns (brief, note). `writer` is "provider/model" of the model that wrote the brief."""
    for s in brief["stories"]:
        enforce_status(s, items)  # free, deterministic, always applied
    if not brief["stories"] or writer == "none":
        return brief, "not fact-checked"
    writer_provider = writer.split("/", 1)[0]
    order = [p for p in config["llm"]["order"] if p != writer_provider] + [writer_provider]
    cfg = {**config, "llm": {**config["llm"], "order": order}}
    try:
        answer, checker = llm.complete(cfg, VERIFY_PROMPT,
                                       lambda summary_chars=None, **_: _build(brief, items, summary_chars or 700))
        checks = _parse(answer)
    except Exception as e:  # noqa: BLE001
        print(f"Fact-check skipped: {str(e)[:300]}")
        return brief, "not fact-checked this run"

    fixed = dropped = 0
    keep = []
    by_story = {c.get("story"): c for c in checks if isinstance(c, dict)}
    for i, s in enumerate(brief["stories"], 1):
        c = by_story.get(i) or {}
        verdict = c.get("verdict", "ok")
        if verdict == "drop":
            dropped += 1
            continue
        if verdict == "fix":
            remove = {int(n) for n in c.get("remove_items") or [] if str(n).isdigit()}
            s["items"] = [n for n in s["items"] if n not in remove] or s["items"]
            if c.get("title"):
                s["title"] = clean_text(str(c["title"]), 200)
            if c.get("summary"):
                s["summary"] = clean_text(str(c["summary"]), 900)
            enforce_status(s, items)
            fixed += 1
        keep.append(s)
    brief["stories"] = keep
    note = f"fact-checked by {checker}"
    if fixed or dropped:
        note += f" ({fixed} corrected, {dropped} removed)"
    print(f"Fact-check: {note}")
    return brief, note
