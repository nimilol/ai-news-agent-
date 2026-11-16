"""Parsers for the official pages in releases.yaml. Each one understands one page layout and returns plain
dicts, so they can be tested against saved copies of the real pages (tests/fixtures/releases/).

Catalog parsers   text -> [{"id", "name", "status", "specs", "url", "text"}]
Changelog parsers text -> [{"uid", "date", "title", "text", "model_ids", "tags", "url"}]
Detail parsers    text -> specs dict (context, output limit, prices, cutoff, modalities, ...)

Spec keys (all optional): context_tokens, max_output_tokens, input_price, output_price (USD per 1M tokens),
knowledge_cutoff, input_modalities, output_modalities, latency, reasoning, description, retirement, status_note.
"""

import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from ..common import clean_text, now_utc
from .identity import content_fingerprint, extract_models, is_quantized

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"], 1)}

# Words that say what kind of change an entry is. Used by the classifier (agent/releases/classify.py).
TAG_PATTERNS = {
    "release": re.compile(r"\b(introduc\w*|launch\w*|releas\w*|unveil\w*|debut\w*|new model|meet |announc\w*|"
                          r"generally available|\(ga\)|\bga\b|available today|now available|is here|arrives?)\b", re.I),
    "update": re.compile(r"\b(updat\w*|upgrad\w*|improv\w*|new snapshot|refresh\w*|better|faster|now supports)\b", re.I),
    "availability": re.compile(r"\b(now available (?:in|on|to|for|through|via)|available (?:in|on|to|for|through|via)|"
                               r"rolling out|roll(?:s|ed)? out|generally available|\(ga\)|expand\w* (?:to|access)|"
                               r"region|free tier|all users|subscribers)\b", re.I),
    "deprecation": re.compile(r"\b(deprecat\w*|retir\w*|sunset\w*|shut(?:ting)? down|end of life|discontinu\w*|"
                              r"no longer available|route[sd]? to)\b", re.I),
    "preview": re.compile(r"\b(preview|beta|experimental|research preview|early access|limited availability)\b", re.I),
    "pricing": re.compile(r"\b(pric\w*|cost\w*|discount\w*|per 1m|per million|/ ?mtok|\$\d)", re.I),
}


def tags_for(*texts):
    text = " ".join(t or "" for t in texts)
    return sorted(k for k, p in TAG_PATTERNS.items() if p.search(text))


# ----------------------------------------------------------------------------------------------- numbers

def parse_tokens(text):
    """'1M tokens' → 1000000, '128K' → 128000, '1,050,000' → 1050000, '500k' → 500000. None if absent."""
    if text is None:
        return None
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?\b", str(text))
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    value *= {"k": 1_000, "m": 1_000_000}.get(unit, 1)
    return int(value) if value >= 1000 else None


def parse_money(text):
    """All dollar amounts in a cell, in order: '$4 / input MTok, $20 / output MTok' → [4.0, 20.0]."""
    return [float(x.replace(",", "")) for x in re.findall(r"\$\s?(\d[\d,]*(?:\.\d+)?)", str(text or ""))]


def _iso(y, m, d):
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except (TypeError, ValueError):
        return None


def parse_human_date(text):
    """'November 12, 2026' / 'Nov 5, 2026' / '2026.10.13' / '12 Nov 2026' → '2026-11-12'."""
    text = str(text or "")
    m = re.search(r"\b(20\d\d)[.\-/](\d{1,2})[.\-/](\d{1,2})\b", text)
    if m:
        return _iso(*m.groups())
    m = re.search(r"\b([A-Za-z]{3,9})\.? (\d{1,2}),? (20\d\d)\b", text)
    if m and m.group(1)[:3].lower() in MONTHS:
        return _iso(m.group(3), MONTHS[m.group(1)[:3].lower()], m.group(2))
    m = re.search(r"\b(\d{1,2}) ([A-Za-z]{3,9}) (20\d\d)\b", text)
    if m and m.group(2)[:3].lower() in MONTHS:
        return _iso(m.group(3), MONTHS[m.group(2)[:3].lower()], m.group(1))
    return None


# ----------------------------------------------------------------------------------------------- markdown

_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def md_plain(cell):
    """A markdown table cell as text: links become their label, code ticks and bold markers dropped."""
    cell = _LINK.sub(r"\1", cell or "")
    return clean_text(re.sub(r"[`*]|\\(?=_)", "", cell))


def md_tables(text):
    """Every markdown table: [{"heading": last heading above it, "header": [...], "rows": [[raw cells]]}]."""
    tables, heading, lines = [], "", (text or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.lstrip().startswith("#"):
            heading = line.lstrip("# ").strip()
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|\s*:?-{1,}", lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            tables.append({"heading": heading, "header": header, "rows": rows})
            continue
        i += 1
    return tables


def _col(header, *names):
    for n, h in enumerate(header):
        low = md_plain(h).lower()
        if any(name in low for name in names):
            return n
    return None


# ----------------------------------------------------------------------------------------------- catalogs

def claude_models_md(text, company=None):
    """platform.claude.com/docs/en/models/overview.md: a comparison table (one column per latest model) and an
    "All models" table (one row per model, linking to /models/<slug>/overview)."""
    entries, by_name = {}, {}
    for t in md_tables(text):
        header = t["header"]
        first = md_plain(header[0]).lower() if header else ""
        if first == "feature":  # comparison table: rows are properties, columns are models
            names = [md_plain(h) for h in header[1:]]
            props = {md_plain(r[0]).lower(): [md_plain(c) for c in r[1:]] for r in t["rows"] if r}
            for col, name in enumerate(names):
                def get(label, col=col):
                    row = next((v for k, v in props.items() if k.startswith(label)), None)
                    return row[col] if row and col < len(row) else None
                mid = (get("claude api id") or "").strip()
                if not mid:
                    continue
                prices = parse_money(get("pricing"))
                specs = {k: v for k, v in {
                    "description": get("description"),
                    "latency": get("comparative latency"),
                    "context_tokens": parse_tokens(get("context window")),
                    "max_output_tokens": parse_tokens(get("max output")),
                    "input_price": prices[0] if prices else None,
                    "output_price": prices[1] if len(prices) > 1 else None,
                    "reasoning": get("thinking"),
                    "knowledge_cutoff": get("reliable knowledge cutoff"),
                    "retirement": get("retirement"),
                }.items() if v}
                by_name[name.lower()] = mid
                entries[mid] = {"id": mid, "name": name, "status": "active", "specs": specs, "url": None}
        elif first == "model":  # "All models": one row per model
            for r in t["rows"]:
                link = _LINK.search(r[0])
                m = re.search(r"/models/([a-z0-9-]+)/overview", link.group(2)) if link else None
                if not m:
                    continue
                name = link.group(1)
                mid = by_name.get(name.lower()) or f"claude-{m.group(1)}"
                row = {md_plain(h).lower(): md_plain(c) for h, c in zip(header, r)}
                inp = parse_money(next((v for k, v in row.items() if k.endswith("input")), ""))
                out = parse_money(next((v for k, v in row.items() if k.endswith("output") and "max" not in k), ""))
                specs = {"context_tokens": parse_tokens(next((v for k, v in row.items() if "context" in k), None)),
                         "max_output_tokens": parse_tokens(next((v for k, v in row.items() if "max output" in k), None)),
                         "input_price": inp[0] if inp else None, "output_price": out[0] if out else None}
                specs = {k: v for k, v in specs.items() if v}
                status = "limited" if "limited availability" in r[0].lower() else "active"
                e = entries.setdefault(mid, {"id": mid, "name": name, "status": status, "specs": {}, "url": None})
                e["url"] = link.group(2)
                e["status"] = status if status != "active" else e["status"]
                e["specs"] = {**specs, **e["specs"]}
    return list(entries.values())


def claude_deprecations_md(text, company=None):
    """model-deprecations.md: one table row per model id with its state and retirement date."""
    out = {}
    for t in md_tables(text):
        header = t["header"]
        c_id = _col(header, "api model name", "model")
        c_state = _col(header, "current state", "state")
        c_ret = _col(header, "retirement", "retired")
        if c_id is None or c_state is None:
            continue
        for r in t["rows"]:
            mid = md_plain(r[c_id]) if c_id < len(r) else ""
            if not re.match(r"^claude-[a-z0-9-]+$", mid):
                continue
            state = md_plain(r[c_state]).lower() if c_state < len(r) else ""
            specs = {"retirement": md_plain(r[c_ret])} if c_ret is not None and c_ret < len(r) else {}
            out.setdefault(mid, {"id": mid, "name": mid, "status": state or "active", "specs": specs, "url": None})
    return list(out.values())


def openai_models_md(text, company=None):
    """developers.openai.com/api/docs/models/all.md: sections of '- [Name](/api/docs/models/<id>.md): text'."""
    out, section = {}, ""
    for line in (text or "").splitlines():
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        m = re.match(r"^\s*-\s*\[([^\]]+)\]\(/api/docs/models/([a-z0-9.\-]+)\.md\):?\s*(.*)$", line)
        if not m:
            continue
        name, mid, desc = m.group(1), m.group(2), clean_text(m.group(3), 300)
        special = re.search(r"Model ID: `([^`]+)`", line)
        mid = special.group(1) if special else mid
        status = "deprecated" if "deprecated" in section else "active"
        e = out.setdefault(mid, {"id": mid, "name": name, "status": status, "specs": {}, "url": None})
        if desc and not e["specs"].get("description"):
            e["specs"]["description"] = desc
        if status == "deprecated":
            e["status"] = "deprecated"
    return list(out.values())


def openai_model_md(text):
    """One OpenAI model page (.md): '- 1,050,000 context window', '- 128,000 max output tokens', pricing table."""
    specs = {}
    for line in (text or "").splitlines():
        s = line.strip("-* ").strip()
        low = s.lower()
        if low.endswith("context window"):
            specs["context_tokens"] = parse_tokens(s)
        elif "max output tokens" in low:
            specs["max_output_tokens"] = parse_tokens(s)
        elif low.endswith("knowledge cutoff"):
            specs["knowledge_cutoff"] = clean_text(s[: -len("knowledge cutoff")])
        elif low.startswith("input modalities:"):
            specs["input_modalities"] = s.split(":", 1)[1].strip()
        elif low.startswith("output modalities:"):
            specs["output_modalities"] = s.split(":", 1)[1].strip()
    m = re.search(r"`?reasoning\.effort`? supports ([\s\S]+?\.)(?:\s|$)", text or "")
    if m:
        specs["reasoning"] = "reasoning effort: " + clean_text(m.group(1).replace("`", ""), 160)
    for t in md_tables(text):
        if _col(t["header"], "metric") is None:
            continue
        for r in t["rows"]:
            label, price = md_plain(r[0]).lower(), parse_money(r[1] if len(r) > 1 else "")
            if price and label == "input" and "input_price" not in specs:
                specs["input_price"] = price[0]
            elif price and label == "output" and "output_price" not in specs:
                specs["output_price"] = price[0]
    return {k: v for k, v in specs.items() if v}


def xai_models_md(text, company=None):
    """docs.x.ai/developers/models.md: pricing tables whose first column is the model id. Models with long-context
    pricing appear on two rows ('grok-4.7 (< 200k prompt tokens)'); the first (base) row is kept."""
    out = {}
    for t in md_tables(text):
        header = t["header"]
        if _col(header, "model") != 0:
            continue
        c_ctx, c_in, c_out = _col(header, "context"), _col(header, "input"), _col(header, "output")
        c_cost = _col(header, "cost")
        for r in t["rows"]:
            mid = re.sub(r"\s*\(.*?\)\s*", "", md_plain(r[0])).strip()
            if not re.match(r"^grok[a-z0-9.\-]*$", mid) or mid in out:
                continue
            specs = {}
            if c_ctx is not None and c_ctx < len(r):
                specs["context_tokens"] = parse_tokens(md_plain(r[c_ctx]))
            if c_in is not None and c_in < len(r) and parse_money(r[c_in]):
                specs["input_price"] = parse_money(r[c_in])[0]
            if c_out is not None and c_out < len(r) and parse_money(r[c_out]):
                specs["output_price"] = parse_money(r[c_out])[0]
            if c_cost is not None and c_cost < len(r):
                specs["price_note"] = md_plain(r[c_cost])
            out[mid] = {"id": mid, "name": mid, "status": "active", "specs": {k: v for k, v in specs.items() if v},
                        "url": None}
    return list(out.values())


def _attrs(tag_text):
    return dict(re.findall(r'(\w+)="([^"]*)"', tag_text))


def gemini_models_md(text, company=None):
    """aistudio.google.com/docs/models.md: model cards ({% card ... href="/docs/models/<id>" %}), bold model codes
    and backticked ids in the 'All ... models' lists."""
    out = {}
    for m in re.finditer(r"\{%\s*card\b([^%]*)%\}", text or ""):
        a = _attrs(m.group(1))
        href = re.search(r"/docs/models/([a-z0-9.\-]+)", a.get("href", ""))
        if not href:
            continue
        mid = href.group(1)
        badge = a.get("badge", "").lower()
        status = "deprecated" if "deprecat" in badge else "preview" if "preview" in badge else "active"
        out[mid] = {"id": mid, "name": a.get("title") or mid, "status": status,
                    "specs": {"description": clean_text(a.get("description", ""), 300)} if a.get("description") else {},
                    "url": None}
    for m in re.finditer(r"\*\*((?:gemini|gemma)-[a-z0-9.\-]+)\*\*|`((?:gemini|gemma)-[a-z0-9.\-]+)`", text or ""):
        mid = m.group(1) or m.group(2)
        out.setdefault(mid, {"id": mid, "name": mid, "status": "active", "specs": {}, "url": None})
    names = dict((mid, name) for name, mid in re.findall(r"\[([^\]]+)\]\(/docs/models/([a-z0-9.\-]+)\)", text or ""))
    for mid, e in out.items():
        if e["name"] == mid and mid in names:
            e["name"] = names[mid]
    return list(out.values())


def gemini_model_md(text):
    """One Gemini model page (.md): token limits, inputs/outputs, knowledge cutoff, latest update."""
    t = text or ""
    specs = {}
    m = re.search(r"(?:Context window[^*]*|Input token limit)\*\*\s*([\d,]+)", t)
    if m:
        specs["context_tokens"] = parse_tokens(m.group(1))
    m = re.search(r"\*\*(?:Max output|Output token limit)\*\*\s*([\d,]+)", t)
    if m:
        specs["max_output_tokens"] = parse_tokens(m.group(1))
    m = re.search(r"\*\*Inputs?\*\*\s*([^\n*]+)", t)
    if m:
        specs["input_modalities"] = clean_text(m.group(1))
    m = re.search(r"\*\*Outputs?\*\*\s*([^\n*]+)", t)
    if m:
        specs["output_modalities"] = clean_text(m.group(1))
    m = re.search(r"Knowledge cutoff(?:\{[^}]*\})?[\s\S]{0,120}?\*\s+([A-Z][a-z]+ \d{4})", t)
    if m:
        specs["knowledge_cutoff"] = m.group(1)
    m = re.search(r"Latest update(?:\{[^}]*\})?[\s\S]{0,120}?\*\s+([A-Z][a-z]+ \d{4})", t)
    if m:
        specs["latest_update"] = m.group(1)
    return {k: v for k, v in specs.items() if v}


def kimi_models_md(text, company=None):
    """platform.kimi.ai/docs/models.md: tables of `model-id` | description, the last one 'Deprecated Models'."""
    out = {}
    for t in md_tables(text):
        if _col(t["header"], "model name", "model") is None:
            continue
        deprecated = "deprecat" in t["heading"].lower()
        for r in t["rows"]:
            mid = md_plain(r[0])
            if not re.match(r"^(kimi|moonshot)[a-z0-9.\-]*$", mid):
                continue
            desc = md_plain(r[1]) if len(r) > 1 else ""
            status = "deprecated" if deprecated or desc.lower() == "deprecated" else "active"
            if "no longer available to newly registered" in desc.lower():
                status = "restricted"
            out[mid] = {"id": mid, "name": mid, "status": status,
                        "specs": {"description": clean_text(desc, 300)} if desc else {}, "url": None}
    return list(out.values())


def alibaba_new_models(html_text, company=None):
    """Alibaba Cloud Model Studio 'newly released models': one heading per model id with a description.
    Other companies' models sold on the platform (pixverse/..., deepseek-...) are skipped: only ids matching
    Qwen's patterns are kept. A paragraph that opens with a model name ("Qwen4-Max is the next-generation ...")
    also counts, because flagship launches are sometimes written that way."""
    patterns = (company or {}).get("_patterns") or []
    soup = BeautifulSoup(html_text or "", "html.parser")
    root = soup.find("main") or soup.find("article") or soup.body or soup
    out, current = {}, None
    for el in root.find_all(["h2", "h3", "h4", "p"]):
        text = clean_text(el.get_text(" ", strip=True))
        if not text:
            continue
        if el.name in ("h2", "h3", "h4"):
            current = None
            if "/" in text or " " in text.strip():
                continue
            if patterns and not extract_models(text, patterns):
                continue
            current = text
            out.setdefault(text, {"id": text, "name": text, "status": "active", "specs": {}, "url": None})
            continue
        if current and not out[current]["specs"].get("description"):
            out[current]["specs"]["description"] = clean_text(text, 400)
            if re.search(r"\bpreview\b", text, re.I):
                out[current]["status"] = "preview"
        elif patterns:
            lead = re.match(r"^\s*([A-Za-z][\w.\-]*(?:-[\w.]+)*)\s+(?:is|are|now)\b", text)
            if lead and extract_models(lead.group(1), patterns):
                name = lead.group(1)
                if name not in out:
                    status = "preview" if re.search(r"\bpreview\b", name + " " + text[:200], re.I) else "active"
                    out[name] = {"id": name, "name": name, "status": status,
                                 "specs": {"description": clean_text(text, 400)}, "url": None}
    return list(out.values())


# ----------------------------------------------------------------------------------------------- changelogs

# A model named right after these words is a comparison, not what the entry is about ("faster than X", "like X").
COMPARISON_CUE = re.compile(r"(?:\bthan|\blike|\bfrom|\bover|\bvs\.?|\bversus|compared (?:to|with)|replac\w*|"
                            r"previous(?:-generation)?|\bunlike|same as|as with|instead of|migrat\w* from|"
                            r"successor (?:to|of)|upgrade (?:over|from))\s+(?:the\s+|our\s+)?$", re.I)


def subject_models(text, patterns):
    """Model names in `text` that are not introduced by a comparison word. A list after one ("like X and Y")
    is excluded as a whole."""
    out, low, pos, excluded_end = [], (text or "").lower(), 0, None
    for name in extract_models(text, patterns):
        at = low.find(name.lower(), pos)
        if at < 0:
            at = low.find(name.lower())
        cue = COMPARISON_CUE.search(text[max(0, at - 40):at])
        chained = excluded_end is not None and re.fullmatch(r"\s*(?:,|and|or|,\s*and)\s*", text[excluded_end:at] or "")
        if cue or chained:
            excluded_end = at + len(name)
        else:
            excluded_end = None
            out.append(name)
        pos = at + len(name) if at >= 0 else pos
    return out


def _entry(date_value, title, text, url, patterns, extra_ids=(), extra_tags=()):
    """One changelog entry. `model_ids` are the models the entry is ABOUT: ids the page marks explicitly
    (a 'Model:' tag, code formatting), else names in the title, else the first name in the text.
    `mentions` are all names found, including older models mentioned in passing (used to find predecessors)."""
    title = _LINK.sub(r"\1", title or "")
    text = _LINK.sub(r"\1", text or "")
    mentions = list(dict.fromkeys(list(extra_ids) + extract_models(f"{title} {text}", patterns)))
    subject = list(dict.fromkeys(extra_ids)) or subject_models(title, patterns) or subject_models(text, patterns)[:1]
    return {"uid": content_fingerprint(date_value or "", title, text[:400]), "date": date_value, "title": title,
            "text": clean_text(text, 3000), "model_ids": subject, "mentions": mentions,
            "tags": sorted(set(tags_for(title, text)) | set(extra_tags)), "url": url}


def openai_changelog_md(text, company=None, url=None):
    """developers.openai.com/api/docs/changelog.md:
        ## November, 2026
        ### Nov 5
        Feature · Model: gpt-6-cyber · API: v1/responses
        body ..."""
    patterns = (company or {}).get("_patterns") or []
    entries, year, cur = [], None, None

    def close():
        if cur:
            body = "\n".join(cur["body"]).strip()
            tagline = cur["tagline"]
            ids = re.findall(r"Model: ([a-z0-9.\-]+)", tagline)
            kind = tagline.split("·")[0].strip().lower() if tagline else ""
            tags = {"feature": ["feature_tag"], "update": ["update"], "fix": ["fix"]}.get(kind, [])
            entries.append(_entry(cur["date"], tagline or body.split("\n")[0][:120], body, url, patterns, ids, tags))

    for line in (text or "").splitlines():
        m = re.match(r"^##\s+([A-Za-z]+),?\s+(20\d\d)\s*$", line)
        if m:
            close()
            cur, year = None, m.group(2)
            continue
        m = re.match(r"^###\s+([A-Za-z]{3,9})\.?\s+(\d{1,2})\s*$", line)
        if m:
            close()
            month = MONTHS.get(m.group(1)[:3].lower())
            cur = {"date": _iso(year, month, m.group(2)) if year and month else None, "tagline": "", "body": []}
            continue
        if cur is None:
            continue
        if not cur["tagline"] and not cur["body"] and re.match(r"^(Feature|Update|Fix|Deprecation|Removal)\b", line):
            cur["tagline"] = line.strip()
        elif line.strip():
            cur["body"].append(line.strip())
    close()
    return entries


def gemini_changelog_md(text, company=None, url=None):
    """aistudio.google.com/docs/changelog.md: '## November 12, 2026 {% id=... %}' followed by bullets that start
    with a bold title ('*   **Gemini 4 Hydrogen (`gemini-4-hydrogen`) generally available (GA)**: Released ...')."""
    patterns = (company or {}).get("_patterns") or []
    entries, day, cur = [], None, None

    def close():
        if cur:
            body = " ".join(cur["lines"])
            title_m = re.match(r"\*\*(.+?)\*\*:?\s*(.*)$", body)
            title, rest = (title_m.group(1), title_m.group(2)) if title_m else (body[:120], body)
            ids = re.findall(r"`((?:gemini|gemma|imagen|veo|lyria)[a-z0-9.\-]*)`", body)
            entries.append(_entry(day, md_plain(title), md_plain(rest), url, patterns, ids))

    for line in (text or "").splitlines():
        m = re.match(r"^##\s+(.+?)\s*(?:\{%.*%\})?\s*$", line)
        if m and parse_human_date(m.group(1)):
            close()
            cur, day = None, parse_human_date(m.group(1))
            continue
        if day is None:
            continue
        m = re.match(r"^\*\s+(.*)$", line)
        if m:
            close()
            cur = {"lines": [m.group(1).strip()]}
        elif cur is not None and line.startswith(" ") and line.strip():
            cur["lines"].append(line.strip())
        elif line.startswith("#"):
            close()
            cur = None
    close()
    return entries


def xai_release_notes_md(text, company=None, url=None, today=None):
    """docs.x.ai/developers/release-notes.md: '## November' month headings (no year) and one '### Title' per
    change. The year is inferred: a month later than the current one belongs to last year. Only the month is
    known, so the date is stored as 'YYYY-MM'."""
    patterns = (company or {}).get("_patterns") or []
    today = today or now_utc().date()
    entries, month_value, cur = [], None, None
    last_month = None
    year = today.year

    def close():
        if cur:
            entries.append(_entry(month_value, cur["title"], " ".join(cur["body"]), url, patterns))

    for line in (text or "").splitlines():
        m = re.match(r"^##\s+([A-Za-z]+)\s*(20\d\d)?\s*$", line)
        if m and m.group(1)[:3].lower() in MONTHS:
            close()
            cur = None
            month = MONTHS[m.group(1)[:3].lower()]
            if m.group(2):
                year = int(m.group(2))
            elif last_month is None:
                year = today.year if month <= today.month else today.year - 1
            elif month > last_month:
                year -= 1  # the list runs newest first: going from January back to December is a year earlier
            last_month = month
            month_value = f"{year:04d}-{month:02d}"
            continue
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m and month_value:
            close()
            cur = {"title": md_plain(m.group(1)), "body": []}
            continue
        if cur is not None and line.strip() and not line.startswith("#"):
            cur["body"].append(md_plain(line.strip()))
    close()
    return entries


def claude_release_rss(feed_items, company=None, url=None):
    """The Claude Platform release-notes feed: one entry per day, each <li> a separate change.
    `feed_items` are feedparser entries."""
    patterns = (company or {}).get("_patterns") or []
    entries = []
    for e in feed_items:
        raw = e.get("content", [{}])[0].get("value", "") if e.get("content") else e.get("summary", "")
        day = parse_human_date(e.get("title", "")) or (
            datetime(*e["published_parsed"][:3]).date().isoformat() if e.get("published_parsed") else None)
        soup = BeautifulSoup(raw or "", "html.parser")
        items = soup.find_all("li") or [soup]
        for li in items:
            text = clean_text(li.get_text(" ", strip=True))
            if not text:
                continue
            ids = [c.get_text(strip=True) for c in li.find_all("code")
                   if re.match(r"^claude-[a-z0-9-]+$", c.get_text(strip=True))]
            first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0][:200]
            entries.append(_entry(day, first, text, e.get("link") or url, patterns, ids))
    return entries


def html_headings(html_text, company=None, url=None):
    """A release-notes page with date headings ('2026.10.13') and one heading per change (Gemini app)."""
    patterns = (company or {}).get("_patterns") or []
    soup = BeautifulSoup(html_text or "", "html.parser")
    root = soup.find("main") or soup.find("article") or soup.body or soup
    entries, day, cur = [], None, None

    def close():
        if cur:
            entries.append(_entry(day, cur["title"], " ".join(cur["body"]), url, patterns))

    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        text = clean_text(el.get_text(" ", strip=True))
        if not text:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            if parse_human_date(text) and len(text) < 40:
                close()
                cur, day = None, parse_human_date(text)
            elif el.name != "h1":
                close()
                cur = {"title": text, "body": []}
        elif cur is not None and len(" ".join(cur["body"])) < 2000:
            cur["body"].append(text)
    close()
    return entries


# ----------------------------------------------------------------------------------------------- repos and APIs

def huggingface_org_models(data, company=None, org=None):
    """Hugging Face API /api/models?author=<org>: the org's newest repos. Format re-uploads are skipped."""
    patterns = (company or {}).get("_patterns") or []
    out = []
    for m in data or []:
        rid = m.get("id") or m.get("modelId") or ""
        name = rid.split("/", 1)[-1]
        if not rid or is_quantized(name) or (patterns and not extract_models(name, patterns)):
            continue
        out.append({"id": rid, "name": name, "status": "active", "url": f"https://huggingface.co/{rid}",
                    "specs": {"created": (m.get("createdAt") or "")[:10], "task": m.get("pipeline_tag")},
                    "model_ids": [name]})
    return out


def github_org_repos(data, company=None, org=None):
    """GitHub API /orgs/<org>/repos?sort=created: only repos named after a model count."""
    patterns = (company or {}).get("_patterns") or []
    out = []
    for r in data or []:
        name = r.get("name") or ""
        if not name or (patterns and not extract_models(name, patterns)):
            continue
        out.append({"id": r.get("full_name") or name, "name": name, "status": "active", "url": r.get("html_url"),
                    "specs": {"created": (r.get("created_at") or "")[:10],
                              "description": clean_text(r.get("description") or "", 300)},
                    "model_ids": [name]})
    return out


def gemini_api_models(data, company=None):
    """generativelanguage.googleapis.com/v1beta/models: {"models": [{"name": "models/gemini-...", ...}]}."""
    out = []
    for m in (data or {}).get("models", []):
        mid = (m.get("name") or "").split("/", 1)[-1]
        if not mid:
            continue
        specs = {"context_tokens": m.get("inputTokenLimit"), "max_output_tokens": m.get("outputTokenLimit"),
                 "description": clean_text(m.get("description") or "", 300)}
        out.append({"id": mid, "name": m.get("displayName") or mid, "status": "active",
                    "specs": {k: v for k, v in specs.items() if v}, "url": None})
    return out


def anthropic_models_api(data, company=None):
    """api.anthropic.com/v1/models: {"data": [{"id", "display_name", "created_at"}]}."""
    return [{"id": m["id"], "name": m.get("display_name") or m["id"], "status": "active", "url": None,
             "specs": {"created": (m.get("created_at") or "")[:10]}}
            for m in (data or {}).get("data", []) if m.get("id")]


CATALOG_PARSERS = {
    "claude_models_md": claude_models_md,
    "claude_deprecations_md": claude_deprecations_md,
    "openai_models_md": openai_models_md,
    "xai_models_md": xai_models_md,
    "gemini_models_md": gemini_models_md,
    "kimi_models_md": kimi_models_md,
    "alibaba_new_models": alibaba_new_models,
    "gemini_api_models": gemini_api_models,
    "anthropic_models_api": anthropic_models_api,
}
CHANGELOG_PARSERS = {
    "openai_changelog_md": openai_changelog_md,
    "gemini_changelog_md": gemini_changelog_md,
    "xai_release_notes_md": xai_release_notes_md,
    "claude_release_rss": claude_release_rss,
    "html_headings": html_headings,
}
DETAIL_PARSERS = {
    "openai_models_md": openai_model_md,
    "gemini_models_md": gemini_model_md,
}
