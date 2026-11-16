"""Model Access Intelligence: where AI models can be used for free or cheaply (independent of /free).

    config.py     modelaccess.yaml: companies, provider rules, platform registry
    sources.py    catalog fetchers (models.dev, OpenRouter, Gemini pricing page, platform checks, Reddit leads)
    normalize.py  catalog listings -> canonical models and access offers with a status each
    db.py         the persistent database (state/modelaccess.json) with change history and alert history
    analyze.py    per-model picks, comparisons, alternatives and the personal leaderboard
    alerts.py     free-access alerts (deduplicated against the alert history)
    render.py     Telegram messages; every one begins with [MODEL ACCESS INTELLIGENCE]
    respond.py    /modelaccess routing (the same routing runs in the Cloudflare bot)
    pipeline.py   refresh, weekly report and bot push
"""
