"""Deterministic classification of what the sources reported.

Observations (one per new announcement post, changelog entry, catalog change, repository or third-party listing)
are grouped by model identity (company + base key, see identity.py) and each group gets:

  category      NEW_MODEL_RELEASE | MAJOR_MODEL_UPGRADE | MODEL_AVAILABILITY | DOCUMENTATION_OR_PRICING_CHANGE |
                PRODUCT_OR_FEATURE_ANNOUNCEMENT | UNCONFIRMED_EARLY_SIGNAL
  verification  OFFICIAL_CONFIRMED | OFFICIAL_UPDATE | UNCONFIRMED | REJECTED_AS_DUPLICATE | REQUIRES_MANUAL_REVIEW

The rules, in order:
  * nothing official                          → UNCONFIRMED_EARLY_SIGNAL / UNCONFIRMED
  * model family+version+tier never seen before, and an official source introduces it (an announcement post
    or changelog entry with release wording, a new catalog listing, or a new repository) → NEW_MODEL_RELEASE /
    OFFICIAL_CONFIRMED. A preview-only listing, or a post that mentions an unknown model without introducing it
    → REQUIRES_MANUAL_REVIEW.
  * a known model with a new snapshot/variant id, or an official "update"/"upgrade" → MAJOR_MODEL_UPGRADE
  * a known model reaching GA, a new API, app, region or tier → MODEL_AVAILABILITY
  * deprecations, retirements, price/limit edits → DOCUMENTATION_OR_PRICING_CHANGE
  * anything else official about a known model → PRODUCT_OR_FEATURE_ANNOUNCEMENT
  * a group whose evidence was already recorded for an earlier release → REJECTED_AS_DUPLICATE
The AI never decides on its own: it can only downgrade a release to REQUIRES_MANUAL_REVIEW when it finds, with a
verbatim quote, that the sources describe something else (see analyze.py).
"""

import re

from . import identity

A, B, C, D, E, F = ("NEW_MODEL_RELEASE", "MAJOR_MODEL_UPGRADE", "MODEL_AVAILABILITY",
                    "DOCUMENTATION_OR_PRICING_CHANGE", "PRODUCT_OR_FEATURE_ANNOUNCEMENT", "UNCONFIRMED_EARLY_SIGNAL")
CONFIRMED, UPDATE, UNCONFIRMED, DUPLICATE, REVIEW = ("OFFICIAL_CONFIRMED", "OFFICIAL_UPDATE", "UNCONFIRMED",
                                                     "REJECTED_AS_DUPLICATE", "REQUIRES_MANUAL_REVIEW")
CATEGORY_LABEL = {A: "New model release", B: "Major model upgrade", C: "Model availability",
                  D: "Documentation or pricing change", E: "Product or feature announcement",
                  F: "Unconfirmed early signal"}
VERIFICATION_LABEL = {CONFIRMED: "OFFICIAL CONFIRMED", UPDATE: "OFFICIAL UPDATE", UNCONFIRMED: "UNCONFIRMED",
                      DUPLICATE: "REJECTED AS DUPLICATE", REVIEW: "REQUIRES MANUAL REVIEW"}
RELEASE_TITLE = re.compile(r"\b(introduc\w*|launch\w*|releas\w*|unveil\w*|meet|announc\w*|debut\w*|"
                           r"now available|generally available|available today|is here|new model)\b", re.I)


def _has(obs, tag):
    return tag in obs.get("tags", [])


def is_release_signal(o):
    """Does this official observation introduce a model (rather than just mention one)?"""
    ev = o["event"]
    if ev in ("listed", "repo_created"):
        return True
    if ev == "announcement":
        return bool(RELEASE_TITLE.search(o.get("title", "")))
    if ev == "changelog":
        return _has(o, "release") or _has(o, "feature_tag")
    return False


def group_observations(observations, companies):
    """{(company, base key): [observations]}. An observation about several models joins several groups."""
    groups = {}
    for o in observations:
        cfg = companies.get(o["company"], {})
        if not o.get("model_ids") and o.get("unidentified"):
            groups.setdefault((o["company"], "unidentified-" + o["fingerprints"][-1][:10]), []).append(o)
            continue
        for mid in o.get("model_ids") or []:
            base = identity.base_key(mid, cfg)
            if base:
                groups.setdefault((o["company"], base), []).append(o)
    return groups


def classify_group(company, base, obs, known_bases, known_exact, company_cfg, existing=None):
    """Returns {"category", "verification", "reasons": [...], "official": [...], "unofficial": [...]}."""
    official = [o for o in obs if o["official"]]
    unofficial = [o for o in obs if not o["official"]]
    reasons = []
    out = {"company": company, "base": base, "official": official, "unofficial": unofficial, "reasons": reasons}

    if base.startswith("unidentified-"):
        reasons.append("an official post seems to introduce a model, but no model name could be read from it")
        return {**out, "category": A, "verification": REVIEW} if official else {**out, "category": F,
                                                                               "verification": UNCONFIRMED}

    if not official:
        reasons.append("only third-party sources so far: " + ", ".join(sorted({o["source"] for o in unofficial})))
        if existing and existing.get("verification") in (CONFIRMED, UPDATE):
            return {**out, "category": F, "verification": DUPLICATE,
                    "reasons": reasons + ["the model is already officially known"]}
        return {**out, "category": F, "verification": UNCONFIRMED}

    fingerprints = {fp for o in official for fp in o.get("fingerprints", [])}
    if existing and existing.get("verification") in (CONFIRMED, UPDATE) and fingerprints and \
            fingerprints <= set(existing.get("fingerprints", [])):
        return {**out, "category": existing.get("classification") or A, "verification": DUPLICATE,
                "reasons": ["every source was already recorded for this release"]}

    release_signals = [o for o in official if is_release_signal(o)]
    known = base in known_bases
    exact_new = [mid for o in official for mid in o.get("model_ids", [])
                 if identity.base_key(mid, company_cfg) == base and identity.exact_key(mid, company_cfg) not in known_exact]
    deprecation = official and all(_has(o, "deprecation") or o.get("status") in ("deprecated", "retired")
                                   for o in official)
    availability = [o for o in official if _has(o, "availability") or o["event"] == "status_change"]

    if deprecation:
        reasons.append("official deprecation or retirement notice")
        return {**out, "category": D, "verification": UPDATE}

    if not known:
        if release_signals:
            kinds = sorted({o["event"] for o in release_signals})
            previews = [o for o in release_signals if o.get("status") == "preview" or _has(o, "preview")]
            texty = [o for o in release_signals if o["event"] in ("announcement", "changelog")]
            reasons.append("not seen in any official catalog before; introduced by: " + ", ".join(
                dict.fromkeys(f"{o['source']} ({o['event']})" for o in release_signals)))
            if previews and len(previews) == len(release_signals) and not texty:
                reasons.append("only listed as a preview so far")
                return {**out, "category": A, "verification": REVIEW, "signal_kinds": kinds}
            return {**out, "category": A, "verification": CONFIRMED, "signal_kinds": kinds}
        reasons.append("an official source mentions a model the agent has not seen before, without introducing it")
        return {**out, "category": A, "verification": REVIEW}

    if existing and existing.get("verification") == UNCONFIRMED and release_signals:
        # An earlier unofficial signal is now confirmed by the company: official takes precedence.
        reasons.append("previously only an unconfirmed signal; now confirmed by " +
                       ", ".join(o["source"] for o in release_signals))
        return {**out, "category": A, "verification": CONFIRMED}

    if exact_new and any(o["event"] in ("listed", "changelog", "announcement") for o in official):
        reasons.append("new snapshot or variant of a known model: " + ", ".join(dict.fromkeys(exact_new)))
        return {**out, "category": B, "verification": UPDATE}
    if any(o["event"] == "changelog" and _has(o, "update") and not _has(o, "fix") for o in official) or \
            any(o["event"] == "announcement" and re.search(r"\b(upgrad\w*|updat\w*|improv\w*)\b", o.get("title", ""), re.I)
                for o in official):
        reasons.append("official update to an existing model")
        return {**out, "category": B, "verification": UPDATE}
    if availability:
        reasons.append("an existing model reaches more users, APIs or regions")
        return {**out, "category": C, "verification": UPDATE}
    if any(o["event"] in ("spec_change", "status_change") or _has(o, "pricing") for o in official):
        reasons.append("documentation, pricing or limit change")
        return {**out, "category": D, "verification": UPDATE}
    reasons.append("official post or note about an existing model")
    return {**out, "category": E, "verification": UPDATE}


def classify(observations, store, companies):
    """Classify every group. Returns a list of results (see classify_group), most important first."""
    groups = group_observations(observations, companies)
    results = []
    for (company, base), obs in groups.items():
        cfg = companies.get(company, {})
        existing = store.release_for(company, base)
        r = classify_group(company, base, obs, store.known_before[company], store.known_exact_before[company], cfg,
                           existing)
        r["existing"] = existing
        results.append(r)
    rank = {A: 0, B: 1, C: 2, F: 3, D: 4, E: 5}
    results.sort(key=lambda r: (r["verification"] == DUPLICATE, rank.get(r["category"], 9)))
    return results


def merge_launches(results):
    """Several new models introduced by the same official post or changelog entry (e.g. "Claude Fable 5.2 and
    Claude Mythos 5.2") become one release with several models, so you get one alert per launch."""
    news = [r for r in results if r["category"] == A and r["verification"] in (CONFIRMED, REVIEW)]
    merged, used = [], set()
    for i, r in enumerate(news):
        if i in used:
            continue
        group = [r]
        fps = {fp for o in r["official"] if o["event"] in ("announcement", "changelog") for fp in o["fingerprints"]}
        for j in range(i + 1, len(news)):
            if j in used or news[j]["company"] != r["company"]:
                continue
            other = {fp for o in news[j]["official"] if o["event"] in ("announcement", "changelog")
                     for fp in o["fingerprints"]}
            if fps & other:
                group.append(news[j])
                used.add(j)
        used.add(i)
        merged.append(group)
    return merged
