"""AI providers. Tries each configured provider (and each of its models) until one answers.

Every free provider here speaks the OpenAI-style chat completions format, so one small function
covers them all. Claude uses the official Anthropic SDK and is only tried when ANTHROPIC_API_KEY is set.
"""

import time

import requests

from .common import env, get

RETRY_WAITS = [5, 20]  # seconds; "busy" errors (429/5xx) are usually gone a few seconds later


class ProviderError(Exception):
    pass


def _chat_completions(base_url, key, model, system, user, json_mode=True, extra_headers=None, max_tokens=8000):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **(extra_headers or {})}
    for wait in RETRY_WAITS + [None]:
        r = requests.post(f"{base_url.rstrip('/')}/chat/completions", json=body, headers=headers, timeout=180)
        if r.status_code not in (429, 500, 502, 503, 504) or wait is None:
            break
        print(f"  {model} is busy (HTTP {r.status_code}), retrying in {wait}s")
        time.sleep(wait)
    if r.status_code == 400 and json_mode:  # some models don't support JSON mode: retry once without it
        return _chat_completions(base_url, key, model, system, user, False, extra_headers, max_tokens)
    if r.status_code != 200:
        raise ProviderError(f"HTTP {r.status_code}: {r.text[:300]}")
    try:
        text = r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as e:
        kind = r.headers.get("content-type", "?")
        raise ProviderError(f"unexpected response from {r.url} ({kind}): {r.text[:300]!r}") from e
    if not text or not text.strip():
        raise ProviderError("empty answer")
    return text


def _openrouter_free_models():
    """Free models on OpenRouter, biggest context window first."""
    data = get("https://openrouter.ai/api/v1/models").json().get("data", [])
    free = [
        m for m in data
        if str(m.get("pricing", {}).get("prompt")) == "0" and str(m.get("pricing", {}).get("completion")) == "0"
        and "text" in str(m.get("architecture", {}).get("output_modalities", ["text"]))
    ]
    free.sort(key=lambda m: m.get("context_length") or 0, reverse=True)
    return [m["id"] for m in free[:4]]


def _claude(key, model, system, user):
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "low"},  # summarising is light work; low effort keeps it cheap
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if a safety filter declines, the API retries on a suitable model
    )
    if response.stop_reason == "refusal":
        raise ProviderError("Claude declined the request")
    text = "".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise ProviderError(f"empty answer (stop_reason={response.stop_reason})")
    return text


def complete(config, system, build_user):
    """Run the prompt on the first provider that works.

    `build_user(max_items, summary_chars)` builds the user message, so providers with small input
    limits can get a shorter list. Returns (answer_text, "provider/model") or raises ProviderError.
    """
    llm = config["llm"]
    errors = []
    for name in llm["order"]:
        p = llm["providers"].get(name)
        if not p:
            continue
        key = env(p["key_env"])
        if not key:
            continue
        user = build_user(p.get("max_items"), p.get("summary_chars"))
        models = list(p.get("models", []))
        if models == ["auto-free"]:
            try:
                models = _openrouter_free_models()
            except Exception as e:  # noqa: BLE001 - any failure just skips this provider
                errors.append(f"{name}: could not list free models ({e})")
                continue
        for model in models:
            try:
                if name == "anthropic":
                    text = _claude(key, model, system, user)
                else:
                    headers = p.get("headers")
                    text = _chat_completions(p["base_url"], key, model, system, user, extra_headers=headers,
                                             max_tokens=p.get("max_tokens", 8000))
                return text, f"{name}/{model}"
            except Exception as e:  # noqa: BLE001 - try the next model/provider
                msg = f"{name}/{model}: {str(e)[:200]}"
                print(f"  AI provider failed: {msg}")
                errors.append(msg)
    raise ProviderError("no AI provider worked. " + (" | ".join(errors) if errors else "No API keys are set."))
