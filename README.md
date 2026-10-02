# AI News Agent

A free agent that follows four AI companies, **Anthropic (Claude), OpenAI (GPT), xAI (Grok) and Zhipu / Z.ai (GLM)**,
and sends you a summary on **Telegram** and/or **email**.

- **Every 6 hours:** reads about 47 sources (official blogs and changelogs, leak trackers, Hacker News, Reddit,
  newsletters, YouTube, the press), keeps only what's new and about your four companies, and has a free AI model
  write a brief: grouped by company, labelled *Official / Report / Rumor*, with links and post ideas.
- **Every hour:** checks only the official sources and pings you instantly when something new appears
  (no AI, so it's fast and always works).
- **Costs $0.** It runs on GitHub Actions (free) and free AI tiers. When you have money, add a Claude API key and
  it switches to Claude automatically.

```
sources (RSS, pages, APIs) ──► filter: new + about our 4 companies ──► free AI writes the brief ──► Telegram / email
                                                                         (falls back to plain headlines if every AI is down)
```

---

## Setup (about 20 minutes, all free)

You'll collect a few keys, then paste them into GitHub as **secrets** (GitHub keeps them hidden; they never appear
in the code).

### 1. Telegram bot (where the briefs arrive)
1. In Telegram, open **@BotFather**, send `/newbot`, and follow the steps. It gives you a **bot token**
   like `123456789:AAH...`. That's `TELEGRAM_BOT_TOKEN`.
2. Open a chat with your new bot and send it any message (e.g. "hi").
3. In a browser, open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` (put your token in place of `<YOUR_TOKEN>`).
   Find `"chat":{"id":123456789` and copy that number: that's `TELEGRAM_CHAT_ID`.

### 2. A free AI key (Google Gemini)
Go to **https://aistudio.google.com/apikey**, sign in with Google, click **Create API key**. That's `GEMINI_API_KEY`.

Optional extra free keys. If one provider is busy or hits its daily limit, the agent tries the next:
| Secret | Where to get it |
|---|---|
| `GROQ_API_KEY` | https://console.groq.com/keys |
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys (the agent picks a free model automatically) |
| `ZAI_API_KEY` | https://z.ai, under API keys (GLM Flash models are free) |

Even with **no** AI key, it still works: GitHub Models (free, built into GitHub Actions) is the last fallback, and if
that fails too you get the plain headlines.

### 3. Email (optional)
Use a Gmail address with 2-Step Verification on, then create an **app password** at
**https://myaccount.google.com/apppasswords**. Your normal password won't work here.
- `EMAIL_ADDRESS`: your Gmail address
- `EMAIL_APP_PASSWORD`: the 16-letter app password
- `EMAIL_TO`: optional, where to send (defaults to `EMAIL_ADDRESS`; several addresses separated by commas)

### 4. Add the secrets to GitHub
In this repository: **Settings → Secrets and variables → Actions → New repository secret**.
Add each one by name (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`, …).

### 5. Turn it on and test
1. Open the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
2. Click **AI news digest → Run workflow**, choose **test-delivery**, and run it. You should get a "connected"
   message on Telegram/email.
3. Run it again with **check-sources** and open the run log: it lists which sources work (✓) and which don't (✗).
4. Run it with **digest** for your first brief. From then on it runs by itself every 6 hours.

> **The first brief is short on purpose.** The first time the agent reads a source, it only reports the last 12 hours
> and quietly remembers everything older, so you don't get a flood of old news.

---

## Day to day

- **Briefs:** 00:17, 06:17, 12:17 and 18:17 UTC. Change the time in `.github/workflows/digest.yml` (`cron`, in UTC).
- **Instant alerts:** hourly from official sources (every 30 minutes is possible if the repository is public; see the comment in `.github/workflows/alerts.yml`). To turn them off, open the **Actions** tab, choose
  **Instant alerts**, and pick **Disable workflow** from the **⋯** menu.
- **Archive:** every brief is saved in `digests/` as a Markdown file, so you have a searchable history of everything
  that happened.
- **Broken sources:** websites change. If a source fails 3 runs in a row, the brief's footer names it. Fix or remove
  its line in `sources.yaml`.

## Customising

- **Sources:** `sources.yaml`. Each line is one source, with comments explaining the fields. Add any RSS feed
  (Substack newsletters: add `/feed` to the address; GitHub releases: add `/releases.atom`).
- **Companies and keywords:** `config.yaml` → `companies`. Add a company with its keywords and the agent starts
  covering it.
- **AI providers and models:** `config.yaml` → `llm`. Providers are tried in `order`; providers without a key are
  skipped. Each one lists several models, so if a free model is retired the next one is used.

## Switching to Claude later

Add a secret named `ANTHROPIC_API_KEY` (from https://platform.claude.com). Claude is first in the provider order,
so the next run uses it; the free providers stay as backup. The default model is Claude Opus 5.5. To spend less,
change `models: [claude-opus-5-5]` to `models: [claude-haiku-4-5]` in `config.yaml`.

## Running it on your own computer (optional)

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...            # and any other keys
python -m agent check-sources        # which sources work from your connection
python -m agent digest --dry-run     # print a brief without sending or saving anything
python -m pytest -q                  # offline tests
```

## Limits to know

- **Free tiers change.** Providers adjust free limits and retire models. That's why there are several providers and
  several models each, plus a no-AI fallback.
- **X/Twitter isn't read directly** (its API costs money). A lot of X news reaches you anyway through TestingCatalog,
  Latent Space/AINews, Reddit, Hacker News and the press. Keep phone notifications on for the key X accounts.
- **Some sites block bots** (OpenAI's help pages sometimes do). Those show up as ✗ in `check-sources`; the rest
  keep working.
- **Free minutes:** a private repository gets 2,000 free GitHub Actions minutes a month. This agent uses roughly
  1,000 (briefs about 240, hourly alerts about 720). A public repository has no limit.
- **GitHub pauses scheduled workflows after 60 days without activity** in public repositories. The agent commits its
  memory after runs, which counts as activity. If it ever stops, re-enable it in the Actions tab.
- **Always open the source before you post.** The AI summarises; it can misread. The *Official / Report / Rumor*
  label tells you how much to trust each story.

## Files

| Path | What it is |
|---|---|
| `sources.yaml` | where the agent looks |
| `config.yaml` | companies, keywords, AI providers, delivery |
| `agent/` | the code: `sources.py` (fetchers), `digest.py` (AI prompt and formatting), `llm.py` (providers), `deliver.py` (Telegram/email), `__main__.py` (the runs) |
| `state/` | the agent's memory of what it already sent (saved by the workflows) |
| `digests/` | archive of every brief |
| `.github/workflows/` | the schedules |
