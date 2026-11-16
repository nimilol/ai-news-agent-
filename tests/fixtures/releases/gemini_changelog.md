> ## AI Agent Context
> Get the complete documentation index at: https://aistudio.google.com/llms.txt
> Use this file to discover all available pages.
> For the full documentation in a single file, fetch: https://aistudio.google.com/llms-full.txt

# Release notes

This page documents updates to the Gemini API.

## November 12, 2026  {% id="11-12-2026" %}

*   **Gemini 4 Hydrogen (`gemini-4-hydrogen`) generally available (GA)**: Released
    [Gemini 4 Hydrogen](/docs/models/gemini-4-hydrogen) (`gemini-4-hydrogen`),
    bringing cutting-edge intelligence to everyone, with speed and efficiency
    for everyday coding and agentic workflows.
    Available on the free tier and at paid-tier
    [pricing](/docs/pricing#gemini-4-hydrogen) of $1.50 per 1M input tokens and
    $7.50 per 1M output tokens, `gemini-4-hydrogen` introduces a fourth
    [`xhigh`](/docs/thinking#understanding-thinking-levels) thinking level alongside `low`,
    `medium`, and default `high`, and is now the default model for the
    [Antigravity agent](/docs/antigravity-agent) in Gemini Managed Agents. See
    [What's new in Gemini 4 Hydrogen](/docs/whats-new-gemini-4-hydrogen) for API
    changes and migration guidance from Gemini 3.8 Flash.

## November 11, 2026  {% id="11-11-2026" %}

*   **Gemini Decision 1 and the Decisions API**: Released
    [Gemini Decision 1](/docs/models/gemini-decision-1) (`gemini-decision-1`) and
    the [Decisions API](/docs/decisions-api) for fast, calibrated binary,
    categorical, and rubric decisions across text, images, audio, video, and
    PDFs with zero output tokens. View [pricing](/docs/pricing#gemini-decision)
    and [rate limits](/docs/rate-limits#gemini-decision), or see the
    [Decisions API guide](/docs/decisions-api) to get started.

## November 10, 2026  {% id="11-10-2026" %}

*   **Gemini 4 Carbon (`gemini-4-carbon`) generally available (GA)**: Released
    [Gemini 4 Carbon](/docs/models/gemini-4-carbon) (`gemini-4-carbon`), our
    most capable reasoning model built for sustained agentic coding, multimodal
    knowledge work, scientific reasoning, and cybersecurity defense, available
    at introductory [pricing](/docs/pricing#gemini-4-carbon) of $2.00 per 1M
    input tokens and $10.00 per 1M output tokens through November 30, 2026. See
    [What's new in Gemini 4 Carbon](/docs/whats-new-gemini-4-carbon) to get started.

## November 6, 2026  {% id="11-06-2026" %}

*   **Gemini Embedding 2 Preview deprecation**:
    [`gemini-embedding-2-preview`](/docs/models/gemini-embedding-2-preview) is
    [deprecated](/docs/deprecations#embedding-deprecated-models). Effective
    November 6, 2026, API requests to `gemini-embedding-2-preview` route to
    [`gemini-embedding-2`](/docs/models/gemini-embedding-2) at
    [Gemini Embedding 2 rates](/docs/pricing#gemini-embedding-2). Update your
    model string to `gemini-embedding-2`. `gemini-embedding-2` uses the same API
    schema, pricing, and embedding space, so no other code changes or vector
    re-indexing are needed.

