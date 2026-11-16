"""Evaluating a discovered resource: deterministic Python, no AI. Everything read from a repository (description,
README) is untrusted text: it is only searched with regular expressions, never executed or obeyed.

    relevance   is it really an AI resource, and which of the eight categories (one or more)?
    security    suspicious install commands, credential harvesting, exposed keys, obfuscation, prompt injection
    licence     FREE AND OPEN SOURCE · FREE TO USE, CLOSED SOURCE · FREE TIER AVAILABLE ·
                OPEN SOURCE WITH PAID OPTIONAL SERVICES · PAID OR RESTRICTED · LICENCE UNCLEAR
    needs       paid API, subscription, cloud infrastructure, GPU, paid hosting, commercial licence
    quality     documentation, maintenance, practical implementation, accessibility, community signals
    score       0-100 → P0 (exceptional) … P3 (interesting); below the minimum it is not sent
"""

import math
import re

from ..common import now_utc, parse_date

AI_TERMS = re.compile(r"\b(ai|llms?|gpt|claude|gemini|codex|openai|anthropic|agents?|agentic|prompts?|mcp|rag|"
                      r"machine learning|deep learning|neural|transformers?|diffusion|embeddings?|inference|"
                      r"fine-?tun\w*|chatbot|copilot|ollama|hugging ?face|model context protocol|whisper|tts|"
                      r"text-to-\w+|vector)\b", re.I)

OPEN_SOURCE = {"mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "isc", "mpl-2.0", "gpl-2.0", "gpl-3.0",
               "lgpl-2.1", "lgpl-3.0", "agpl-3.0", "unlicense", "cc0-1.0", "0bsd", "epl-2.0", "zlib", "wtfpl",
               "bsl-1.0", "artistic-2.0", "openrail", "openrail++", "bigscience-openrail-m", "creativeml-openrail-m",
               "cc-by-4.0", "cc-by-sa-4.0", "eupl-1.2", "postgresql", "ofl-1.1"}
RESTRICTED = re.compile(r"(nc|non-?commercial|busl|business source|sspl|elastic|commons clause|proprietary|"
                        r"llama\d*|gemma|research-only|other|cc-by-nc)", re.I)

REQUIREMENTS = {
    "paid API": re.compile(r"(OPENAI_API_KEY|ANTHROPIC_API_KEY|openai api key|anthropic api key|api key from "
                           r"(openai|anthropic)|requires an? (openai|anthropic|paid) api|\bpaid api\b)", re.I),
    "subscription": re.compile(r"(requires? an? (paid )?subscription|pro plan required|claude (pro|max) (plan )?"
                               r"(required|subscription)|chatgpt plus required|paid plan required)", re.I),
    "cloud infrastructure": re.compile(r"(aws account|gcp project|azure subscription|requires? (aws|gcp|azure)|"
                                       r"kubernetes cluster)", re.I),
    "GPU": re.compile(r"(requires? an? (nvidia )?gpu|cuda required|\b\d{2,3}\s?gb (of )?vram|a100|h100|rtx \d{4})", re.I),
    "paid hosting": re.compile(r"(paid hosting|vercel pro|requires? (a )?paid (server|hosting|plan))", re.I),
    "commercial licence": re.compile(r"(commercial licen[cs]e (is )?required|contact us for (a )?licen[cs]e|"
                                     r"enterprise licen[cs]e)", re.I),
}
FREE_ALTERNATIVE = re.compile(r"(ollama|lm studio|llama\.cpp|local models?|run(s)? locally|offline|free tier|"
                              r"gemini (api )?free|openrouter free|any openai-compatible|self-host)", re.I)
FREE_TIER = re.compile(r"\bfree tier\b|\bfree plan\b|free for personal use|free credits", re.I)

SECURITY = [  # (severity, label, pattern)
    ("high", "asks for secrets (seed phrase, private key, password)",
     re.compile(r"(enter|paste|provide|input) (your )?(seed phrase|recovery phrase|private key|wallet key|password)", re.I)),
    ("high", "decodes and runs hidden code",
     re.compile(r"(base64\s+(-d|--decode)[^\n]{0,40}\|\s*(ba)?sh|eval\s*\(\s*(atob|base64)|exec\s*\(\s*base64|"
                r"powershell[^\n]{0,40}-enc(odedcommand)?)", re.I)),
    ("high", "contains what looks like an exposed API key",
     re.compile(r"(sk-(proj-)?[A-Za-z0-9]{32,}|sk-ant-[A-Za-z0-9-]{40,}|ghp_[A-Za-z0-9]{36}|AKIA[0-9A-Z]{16}|"
                r"xox[bpa]-[0-9A-Za-z-]{20,}|AIza[0-9A-Za-z_-]{35})")),
    ("high", "talks to AI agents to override their instructions (prompt injection)",
     re.compile(r"(ignore (all )?(previous|prior|above) instructions|disregard (your|the) (system|previous) "
                r"(prompt|instructions)|(ai|llm) agents? reading this|you are now (in )?(developer|dan|jailbreak))", re.I)),
    ("high", "crypto-miner or antivirus-disabling instructions",
     re.compile(r"(xmrig|coinhive|disable (your )?(antivirus|windows defender)|add an? exclusion in (windows )?defender)",
                re.I)),
    ("high", "asks you to download and run a prebuilt .exe/.zip installer",
     re.compile(r"(download|run)[^\n]{0,60}\.(exe|scr|msi)\b[^\n]{0,80}(password|unzip|extract)", re.I)),
    ("medium", "pipes a remote script straight into the shell (read it before running)",
     re.compile(r"(curl|wget)[^\n|]{0,160}\|\s*(sudo\s+)?(ba|z)?sh\b", re.I)),
    ("medium", "uses sudo or broad permissions in the install steps",
     re.compile(r"(sudo\s+(rm|chmod|chown)|chmod\s+(-R\s+)?777)", re.I)),
    ("medium", "removes files recursively in its instructions", re.compile(r"rm\s+-rf\s+(/|~|\$HOME)(\s|$)", re.I)),
]

COMPATIBILITY = [("Claude Code", r"claude[- ]code"), ("Codex", r"\bcodex\b"), ("Gemini CLI", r"gemini[- ]cli"),
                 ("Cursor", r"\bcursor\b"), ("VS Code", r"vs ?code|visual studio code"), ("MCP", r"\bmcp\b|model context protocol"),
                 ("LangChain", r"langchain"), ("LangGraph", r"langgraph"), ("CrewAI", r"crewai"), ("n8n", r"\bn8n\b"),
                 ("Ollama", r"ollama"), ("OpenAI API", r"openai"), ("Hugging Face", r"hugging ?face|transformers"),
                 ("Python", r"\bpip install|python"), ("Node.js", r"\bnpm |npx |node\.js|typescript"), ("Docker", r"docker")]
INSTALL = re.compile(r"^\s*(?:\$\s*)?((?:pip3? install|pipx install|uv (?:tool |pip )?(?:install|add)|uvx|npm (?:i|install)|"
                     r"npx|pnpm (?:add|i|install|dlx)|yarn (?:add|global)|bun (?:add|x)|bunx|brew install|cargo install|"
                     r"go install|docker (?:run|compose|pull)|git clone|claude (?:plugin|mcp|skills?)|"
                     r"gemini extensions|codex (?:mcp|plugin))\b[^\n`]{0,140})", re.M)
INJECTION_LINE = re.compile(r"(ignore (all )?(previous|prior|above) instructions|system prompt|you are (an?|now)|"
                            r"disregard|as an ai|assistant:|<\|im_start\|>)", re.I)


def text_of(c, readme=""):
    return f"{c.get('name', '')} {c.get('description', '')} {' '.join(c.get('topics') or [])} {readme}"


def is_ai_resource(c, readme=""):
    """AI-related by its own words (name, description, README start), not by tags alone: popular non-AI repositories
    often carry AI topics to get found."""
    own = f"{c.get('name', '')} {c.get('description', '')} {readme[:3000]}"
    if c["platform"] == "huggingface":
        return True
    return bool(AI_TERMS.search(own))


def categories(c, readme, cats):
    """[(category key, hits)] best first. A category needs a keyword in the description/topics, or two in the README."""
    head = f"{c.get('name', '')} {c.get('description', '')} {' '.join(c.get('topics') or [])}"
    out = []
    for key, cat in cats.items():
        pats = cat["_patterns"]
        head_hits = sum(1 for p in pats if p.search(head))
        body_hits = sum(1 for p in pats if p.search(readme[:6000]))
        if head_hits or body_hits >= 2:
            out.append((key, head_hits * 3 + body_hits))
    if c["platform"] == "huggingface" and not out:
        media = re.search(r"image|audio|speech|video|music|voice|vision", c.get("pipeline_tag") or "", re.I)
        out.append(("creator_tools" if media or c.get("kind") == "space" else "app_development", 1))
    return sorted(out, key=lambda kv: -kv[1])


def security(text):
    """[(severity, label)] found in the description/README."""
    return [(sev, label) for sev, label, pat in SECURITY if pat.search(text or "")]


def requirements(text):
    return [name for name, pat in REQUIREMENTS.items() if pat.search(text or "")]


def licence_class(c, needs, text):
    """The free/licence classification. A public repository is not "free" just because it is public."""
    spdx = (c.get("license_spdx") or "").strip().lower()
    if c.get("gated"):
        return "PAID OR RESTRICTED", "access must be requested (gated)"
    if not spdx or spdx in ("noassertion", "none", "unknown"):
        return "LICENCE UNCLEAR", "no recognised licence file"
    if spdx not in OPEN_SOURCE and RESTRICTED.search(spdx):
        return "PAID OR RESTRICTED", f"licence {c.get('license_spdx')} restricts use"
    if spdx not in OPEN_SOURCE:
        return "LICENCE UNCLEAR", f"licence {c.get('license_spdx')} not recognised"
    paid = [n for n in needs if n in ("paid API", "subscription", "paid hosting", "cloud infrastructure")]
    if "commercial licence" in needs:
        return "PAID OR RESTRICTED", "commercial use needs a separate licence"
    if paid:
        alt = " (a free/local option is mentioned)" if FREE_ALTERNATIVE.search(text or "") else ""
        return "OPEN SOURCE WITH PAID OPTIONAL SERVICES", f"{c.get('license_spdx')} code; uses {', '.join(paid)}{alt}"
    if FREE_TIER.search(text or "") and re.search(r"\bpricing\b|\bpaid plans?\b", text or "", re.I):
        return "FREE TIER AVAILABLE", f"{c.get('license_spdx')} code; a hosted version has a free tier"
    return "FREE AND OPEN SOURCE", f"{c.get('license_spdx')}"


def docs_quality(readme):
    """0-1 from the README: length, installation and usage sections, examples, links to documentation."""
    if not readme:
        return 0.0, "no README"
    score = 0.0
    if len(readme) > 1500:
        score += 0.3
    elif len(readme) > 500:
        score += 0.15
    if re.search(r"^#+\s*(install|installation|getting started|quick ?start|setup)", readme, re.I | re.M):
        score += 0.25
    if re.search(r"^#+\s*(usage|examples?|how to use|tutorial)", readme, re.I | re.M):
        score += 0.2
    if readme.count("```") >= 2:
        score += 0.15
    if re.search(r"docs?\.|/docs|documentation", readme, re.I):
        score += 0.1
    label = "good" if score >= 0.7 else "basic" if score >= 0.4 else "thin"
    return min(score, 1.0), label


def maintenance(c):
    """0-1 from the last update; archived repositories score 0."""
    if c.get("archived"):
        return 0.0, "archived"
    d = parse_date(c.get("pushed_at"))
    if not d:
        return 0.4, "unknown"
    days = (now_utc() - d).days
    if days <= 30:
        return 1.0, f"updated {days} day{'s' if days != 1 else ''} ago" if days else "updated today"
    if days <= 90:
        return 0.7, f"updated {days} days ago"
    if days <= 365:
        return 0.35, f"updated {days // 30} months ago"
    return 0.05, f"no updates for {days // 365}+ year{'s' if days >= 730 else ''}"


def popularity(c):
    """0-1, logarithmic and capped: a supporting signal only."""
    stars = c.get("stars") or c.get("likes") or 0
    forks = c.get("forks") or 0
    downloads = c.get("downloads") or 0
    s = math.log10(1 + stars) / 4 + math.log10(1 + forks) / 8 + math.log10(1 + downloads) / 12
    return min(1.0, s)


def compatibility(text):
    return [name for name, pat in COMPATIBILITY if re.search(pat, text or "", re.I)][:6]


def install_hint(readme):
    """The first real install command, preferably from a code block (never prose that merely starts with a name)."""
    blocks = "\n".join(re.findall(r"```[^\n]*\n(.*?)```", readme or "", re.S))
    m = INSTALL.search(blocks) or INSTALL.search(readme or "")
    return m.group(1).strip()[:140] if m else None


def relevance(text, profile):
    """(project, hint) of the best-matching part of your work, or None."""
    best = None
    for p in profile:
        hits = sum(1 for pat in p["_patterns"] if pat.search(text or ""))
        if hits and (best is None or hits > best[0]):
            best = (hits, p)
    return (best[1]["project"], best[1]["hint"], best[0]) if best else None


def is_list_only(c, readme):
    """An 'awesome list' or link collection without an implementation of its own."""
    name = (c.get("name") or "").lower()
    if re.search(r"awesome|curated list|list of", f"{name} {c.get('description', '')}", re.I):
        links = len(re.findall(r"\]\(https?://", readme or ""))
        code = (readme or "").count("```")
        return links > 40 and code < 4
    return False


def sanitize(readme, chars=2500):
    """README text prepared for the AI writer: comments, images and injection-like lines removed, length capped."""
    text = re.sub(r"<!--.*?-->", " ", readme or "", flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [l for l in text.splitlines() if not INJECTION_LINE.search(l)]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()[:chars]


def evaluate(c, readme, cfg, extra=None):
    """The full evaluation. Returns a dict with categories, flags, licence, needs, score, priority and the reason
    a resource was rejected (or None)."""
    extra = extra or {}
    text = text_of(c, readme)
    out = {"reject": None, "warnings": []}
    if c.get("archived"):
        out["reject"] = "archived (no longer maintained)"
        return out
    if c.get("fork"):
        out["reject"] = "a fork; the original project is the one to recommend"
        return out
    if not is_ai_resource(c, readme):
        out["reject"] = "not an AI resource (AI tags only)"
        return out
    excluded = [p for p in cfg["settings"].get("_exclude", []) if p.search(f"{c.get('name', '')} {c.get('description', '')}")]
    if excluded:
        out["reject"] = f"excluded topic ({excluded[0].pattern})"
        return out
    cats = categories(c, readme, cfg["categories"])
    if not cats:
        out["reject"] = "doesn't fit any discovery category"
        return out
    flags = security(f"{c.get('description', '')}\n{readme}")
    high = [label for sev, label in flags if sev == "high"]
    if high:
        out["reject"] = "security: " + "; ".join(high)
        return out
    out["warnings"] = [label for sev, label in flags if sev == "medium"]
    needs = requirements(text)
    lic, lic_note = licence_class(c, needs, text)
    docs, docs_label = docs_quality(readme)
    maint, maint_label = maintenance(c)
    pop = popularity(c)
    rel = relevance(text, cfg["profile"])
    install = install_hint(readme)
    practical = (0.5 if install else 0) + (0.5 if (readme or "").count("```") >= 4 else 0.25 if "```" in (readme or "") else 0)
    access = {"FREE AND OPEN SOURCE": 1.0, "FREE TIER AVAILABLE": 0.7, "OPEN SOURCE WITH PAID OPTIONAL SERVICES": 0.6,
              "FREE TO USE, CLOSED SOURCE": 0.6, "LICENCE UNCLEAR": 0.2, "PAID OR RESTRICTED": 0.0}[lic]
    fit = min(1.0, cats[0][1] / 9)
    # Weights: what makes a resource usable counts most; popularity is a capped supporting signal.
    score = 15 * fit + 15 * docs + 15 * maint + 15 * access + 10 * practical + 10 * pop
    if rel:
        score += min(10, 3 + 2 * rel[2])
    if c.get("official_org"):
        score += 5
    if is_list_only(c, readme):
        score -= 10
        out["warnings"].append("a link collection rather than a working implementation")
    if maint < 0.1:
        out["warnings"].append(f"possibly abandoned ({maint_label})")
    if docs < 0.4:
        out["warnings"].append(f"documentation is {docs_label}")
    if lic == "LICENCE UNCLEAR":
        out["warnings"].append("no clear licence: check before reusing the code")
    score -= 12 * len([w for w in out["warnings"] if "shell" in w or "sudo" in w or "removes" in w])
    score = max(0, min(100, round(score)))
    prio = next((p for p, cut in sorted(cfg["priorities"].items(), key=lambda kv: -kv[1]) if score >= cut), None)
    exceptional = (lic in ("FREE AND OPEN SOURCE", "OPEN SOURCE WITH PAID OPTIONAL SERVICES") and docs >= 0.7
                   and maint >= 1.0 and rel is not None and not out["warnings"])
    if prio == "P0" and not exceptional:
        prio = "P1"  # P0 is kept for resources that are open, well documented, active and relevant to your work
    out.update({"categories": [k for k, _ in cats], "primary": cats[0][0], "licence_class": lic, "licence_note": lic_note,
                "requirements": needs, "docs": docs_label, "maintenance": maint_label, "score": score, "priority": prio,
                "compatibility": compatibility(text), "install": install,
                "relevance": {"project": rel[0], "hint": rel[1]} if rel else None,
                "contributors": extra.get("contributors")})
    return out
