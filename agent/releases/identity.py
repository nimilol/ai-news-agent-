"""Model identity: turns model names and ids into stable keys, so "Claude Opus 5.5", "claude-opus-5-5" and
"anthropic/claude-opus-5-5" are recognised as the same model, and a dated snapshot or a "-preview" /
"-highspeed" variant is recognised as belonging to its base model.

    family   the name without version: "claude-opus", "gpt", "gpt-image", "gemini", "kimi-k", "qwen", "grok"
    version  "5.5", "6.1", "4.20", "3.8" (decimal-style: 4.20 sorts before 4.3, as labs mean it)
    tier     what follows the version: "terra", "hydrogen", "max", "pro", "omni-flash"
    base key family-version-tier without snapshots/variants/sizes: the identity of a release
"""

import hashlib
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit

# Stripped from the base key: they mark a build or packaging of the same model, not a different model.
VARIANT_WORDS = {"preview", "latest", "exp", "experimental", "instruct", "it", "chat", "base", "hf", "highspeed",
                 "reasoning", "non", "fp8", "fp16", "bf16", "gguf", "awq", "gptq", "int4", "int8", "mlx", "bnb",
                 "nvfp4", "w4a16", "w8a8", "4bit", "8bit", "onnx", "eagle", "eagle3", "draft"}
# Re-uploads in another format: never a release of their own.
QUANTIZED = re.compile(r"(?:^|[-_.])(fp8|fp16|bf16|gguf|awq|gptq|int4|int8|mlx|bnb|nvfp4|w4a16|w8a8|4bit|8bit|onnx|"
                       r"eagle3?|draft|quantized|qat|q[2-8](?:_[0-9a-z]+)*)(?:$|[-_.])", re.I)
_SIZE = re.compile(r"^(?:a?\d+(?:\.\d+)?[bm]|\d+x\d+(?:\.\d+)?b|e\d+b)$")          # 7b, a22b, 0.6b, 8x7b, e4b
_MMDD = re.compile(r"^(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$")                    # 0905, 1017
_YYMM = re.compile(r"^2\d(?:0[1-9]|1[0-2])$")                                         # 2507 (Qwen: 2025-07)
_FUSED = re.compile(r"^([a-z]+)(\d+(?:\.\d+)*)$")                                    # qwen3.8, k3, v1
_NUM = re.compile(r"^\d+(?:\.\d+)*$")
VENDOR_PREFIX = re.compile(r"^[a-z0-9_.-]+/")


def _tokens(raw):
    text = VENDOR_PREFIX.sub("", str(raw).strip().lower().strip("`'\" .,;:()"))
    text = re.sub(r"[\s_/]+", "-", text)
    text = re.sub(r"[^a-z0-9.-]", "", text)
    return [t for t in text.strip("-.").split("-") if t and t != "."]


def parse(raw, company_cfg=None):
    """Split a model name or id into family / version / tier / snapshot. Returns a dict."""
    cfg = company_cfg or {}
    toks = _tokens(raw)
    if not toks:
        return {"family": "", "version": "", "tier": "", "snapshot": "", "variants": [], "raw": str(raw)}
    # Bare names inside a company's own post ("Opus 5" in an Anthropic post).
    prefix = cfg.get("family_prefix")
    if prefix and toks[0] != prefix and toks[0] in cfg.get("tier_into_family", []):
        toks = [prefix] + toks
    out = []
    for t in toks:
        m = _FUSED.match(t)
        if m and not _SIZE.match(t):
            out += [m.group(1), m.group(2)]
        else:
            out.append(t)
    toks = out

    family, version, rest = [], [], []
    i = 0
    while i < len(toks) and not _NUM.match(toks[i]) and not (family and _SIZE.match(toks[i])):
        family.append(toks[i])
        i += 1
    while i < len(toks) and _NUM.match(toks[i]) and len(version) < 3:
        t = toks[i]
        if len(t) == 8 or (version and len(t) == 4 and (_MMDD.match(t) or _YYMM.match(t))):
            break  # a snapshot date, not part of the version
        if version and len(t) == 4 and t.startswith("20"):
            break  # a year (snapshot like 2026-07-15)
        version.append(t)
        i += 1
    rest = toks[i:]

    snapshot, variants, tier = [], [], []
    j = 0
    while j < len(rest):
        t = rest[j]
        if re.fullmatch(r"\d{8}", t) or ((_MMDD.match(t) or _YYMM.match(t)) and version):
            snapshot.append(t)
        elif re.fullmatch(r"20\d\d", t) and j + 2 < len(rest) and re.fullmatch(r"\d\d", rest[j + 1]) \
                and re.fullmatch(r"\d\d", rest[j + 2]):
            snapshot.append("-".join(rest[j:j + 3]))
            j += 2
        elif re.fullmatch(r"0[1-9]|1[0-2]", t) and j + 1 < len(rest) and \
                re.fullmatch(r"20\d\d|0[1-9]|[12]\d|3[01]", rest[j + 1]):
            snapshot.append(f"{t}-{rest[j + 1]}")      # Gemini style: -11-2025 (month-year) or -05-20 (month-day)
            j += 1
        elif _SIZE.match(t):
            variants.append(t)
        elif t in VARIANT_WORDS:
            variants.append(t)
        else:
            tier.append(t)
        j += 1

    # Old-style ids: "claude-3-5-sonnet" → family claude-sonnet, version 3.5.
    into = cfg.get("tier_into_family", [])
    if tier and tier[0] in into and len(family) == 1:
        family.append(tier.pop(0))
    if len(version) == 1 and "." in version[0]:
        version_str = version[0]
    else:
        version_str = ".".join(version)
    return {"family": "-".join(family), "version": version_str, "tier": "-".join(tier),
            "snapshot": "-".join(snapshot), "variants": variants, "raw": str(raw)}


def base_key(raw, company_cfg=None):
    """The identity of the model a name refers to (snapshots, variants and sizes removed)."""
    p = raw if isinstance(raw, dict) else parse(raw, company_cfg)
    parts = [p["family"], p["version"].replace(".", "-"), p["tier"]]
    return "-".join(x for x in parts if x)


def exact_key(raw, company_cfg=None):
    """The identity of this exact id, keeping snapshot and variant (used to spot new snapshots)."""
    p = raw if isinstance(raw, dict) else parse(raw, company_cfg)
    parts = [base_key(p), p["snapshot"], "-".join(v for v in p["variants"] if not _SIZE.match(v))]
    return "-".join(x for x in parts if x)


def version_value(version):
    """Sortable value: "4.20" → (4, 0.20), "4.3" → (4, 0.3), so 4.20 < 4.3 as decimal versions."""
    if not version:
        return None
    parts = version.split(".")
    try:
        major = int(parts[0])
        minor = Decimal("0." + "".join(parts[1:])) if len(parts) > 1 else Decimal(0)
    except (ValueError, InvalidOperation):
        return None
    return (major, minor)


def display_name(raw):
    """A readable name for an id that has no official display name: "gpt-6.1-terra" → "GPT-6.1 Terra",
    "kimi-k3.1-pro" → "Kimi K3.1 Pro", "qwen3.8-omni-flash" → "Qwen3.8 Omni Flash"."""
    text = str(raw).strip()
    if " " in text:
        return text
    p = parse(text)
    if not p["family"]:
        return text
    words = [w.upper() if w in ("gpt", "glm", "vl", "tts", "asr", "api") else w.capitalize()
             for w in p["family"].split("-")]
    low = VENDOR_PREFIX.sub("", text.lower())
    last = p["family"].split("-")[-1]
    if p["version"] and re.search(re.escape(last + p["version"]), low):
        words[-1] += p["version"]            # written fused in the id: qwen3.8, k3.1
        name = " ".join(words)
    elif words[0] == "GPT" and p["version"]:
        name = "-".join(words) + "-" + p["version"]
    else:
        name = " ".join(words) + (f" {p['version']}" if p["version"] else "")
    if p["tier"]:
        name += " " + " ".join(w.capitalize() for w in p["tier"].split("-"))
    return name


def is_quantized(name):
    return bool(QUANTIZED.search(str(name)))


def extract_models(text, patterns):
    """Every model mention in `text` matching the company's compiled patterns, in order, without repeats."""
    found = []
    for pat in patterns:
        for m in pat.finditer(text or ""):
            value = m.group(0).strip(" .,;:-`'\"")
            if value and value.lower() not in (f.lower() for f in found):
                found.append(value)
    # Drop a mention that is only part of a longer one ("gpt-6" in "gpt-6-luna", "Fable 5.2" in "Claude Fable 5.2").
    lowered = [f.lower() for f in found]
    return [f for f, low in zip(found, lowered)
            if not any(o != low and (o.startswith(low + "-") or o.endswith(" " + low)) for o in lowered)]


def url_fingerprint(url):
    """The same page under different addresses (tracking parameters, /en/ prefixes, trailing slash, www)."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    path = re.sub(r"/(?:en|en-us|en-gb)(?=/)", "", parts.path.lower()).rstrip("/") or "/"
    return urlunsplit(("https", host, path, "", ""))


def content_fingerprint(*texts):
    norm = re.sub(r"[^a-z0-9]+", " ", " ".join(t or "" for t in texts).lower()).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:16]
