# AI News Agent

A free agent that follows four AI companies, **Anthropic (Claude), OpenAI (GPT), xAI (Grok) and Zhipu / Z.ai (GLM)**,
and sends you a summary on **Telegram** and/or **email**.

- **Every 6 hours:** reads about 42 sources (official blogs and changelogs, leak trackers, Hacker News, Reddit,
  newsletters, YouTube, the press), keeps only what's new and about your four companies, and has a free AI model
  write a brief: grouped by company, labelled *Official / Report / Rumor*, with links and post ideas.
- **Every hour:** checks the official sources plus the early-signal sources (TestingCatalog leaks, new and stealth
  models on OpenRouter, new model listings on models.dev) and the free-model sources (new free models on OpenRouter
  and in OpenCode), and pings you on Telegram as soon as something new appears (no AI, so it's fast and always works).
- **New free models:** the brief has a "🆓 New free models you can use" section for models that just became free on
  OpenRouter or in OpenCode, from any company.
- **Every 3 hours:** "🔥 Buzz", something to talk about: the most talked-about things on **GitHub and Hugging Face**
  right now. Trending repos (AI first, but a non-AI repo that's blowing up gets in too), new models and upgrades
  (extra focus on Claude, OpenAI, GLM, Kimi, MiniMax, Grok, DeepSeek, Qwen, Gemini/Gemma and Llama), uncensored
  models, trending Spaces and papers, the Hugging Face blog, plus news searches for free/limited-time offers and
  censorship. Each pick says what it is, why people are talking, and a line you could post.
- **Every morning:** "🛠 Builder picks", the most useful trending AI tools and repos (GitHub Trending, fast-rising
  new repos, Hugging Face, Show HN), each with what it does and who it helps.
- **Every Sunday:** a weekly recap of the biggest stories, with recap post ideas.
- **Accuracy built in:** reads the full article for the top stories, fact-checks the brief with a second AI,
  shows how many independent sources back each story (⚠️ for single-source claims), the time it first appeared,
  blocks low-quality sites, never labels press reports as "Official", and doesn't repeat stories already sent.
- **Watches quiet changes:** model lists, API prices, deprecation dates, plan pages and status pages at Anthropic,
  OpenAI, xAI and Z.ai, so unannounced price changes, new models and retirements reach you.
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

**You need at least one AI key** for written summaries. Without one (or if every provider is down) the agent still
runs and sends the plain headlines with links, so a run is never lost.

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
4. Optional: run it with **preview**. The run log shows a full brief written by the AI, but nothing is sent and the
   agent's memory isn't changed, so you can preview as often as you like.
5. Optional: run it with **test-ai** to check every AI key you added (each gets a tiny request).
6. Run it with **digest** for your first brief. From then on it runs by itself every 6 hours.

> **The first brief is short on purpose.** The first time the agent reads a source, it only reports the last 12 hours
> and quietly remembers everything older, so you don't get a flood of old news.

---

## Day to day

- **Buzz:** every 3 hours at :47 past (00:47, 03:47, … UTC; `.github/workflows/buzz.yml`). The labs it watches,
  the GitHub topics and the offer/censorship news searches are in `config.yaml` → `buzz`. Run it by hand with
  **Actions → Buzz → Run workflow** (`preview` prints it in the log without sending).
- **Builder picks:** every morning at 06:41 UTC (`.github/workflows/builders.yml`). Topics for the rising-repo
  search are in `config.yaml` → `builders`.
- **Weekly recap:** Sundays at 17:53 UTC (`.github/workflows/weekly.yml`).
- **Briefs:** 00:17, 06:17, 12:17 and 18:17 UTC. Change the time in `.github/workflows/digest.yml` (`cron`, in UTC).
- **Instant alerts:** hourly from official, early-signal and free-model sources (every 30 minutes is possible if the repository is public; see the comment in `.github/workflows/alerts.yml`). To turn them off, open the **Actions** tab, choose
  **Instant alerts**, and pick **Disable workflow** from the **⋯** menu.
- **Archive:** every brief is saved in `digests/` as a Markdown file, so you have a searchable history of everything
  that happened.
- **Broken sources:** websites change. If a source fails 3 runs in a row, the brief's footer names it. Fix or remove
  its line in `sources.yaml`.

## Ask your agent

Send these to your bot in Telegram (they also appear in the bot's **/** menu):

| Command | What you get |
|---|---|
| `/free` | Every model you can use for free right now on OpenRouter and in OpenCode, newest first (🆕 = added in the last 14 days) |
| `/latest` | The most recent news brief again |
| `/repos` | Today's Builder picks |
| `/buzz` | The latest Buzz: what's hot on GitHub and Hugging Face |
| `/ask your question` | An answer from this week's briefs, e.g. `/ask what did OpenAI change this week?` |
| `/news` | Run a fresh news check now (instant bot only) |
| `/help` | The list of commands |

Without the instant bot, the agent reads your messages on each hourly alert check, so **replies take up to an
hour**. With the instant bot (next section) replies take seconds. Only your own chat (`TELEGRAM_CHAT_ID`) gets
answers.

## Instant replies (Cloudflare, free)

This puts a tiny always-on bot on Cloudflare Workers (free plan) so your commands are answered in seconds.
Your GitHub agent keeps doing the research and sends the bot the latest brief, picks and free-model list.

1. Create a free account at **https://dash.cloudflare.com/sign-up** and verify your email.
2. In the dashboard, open **Workers & Pages** once. If it asks you to choose a `workers.dev` subdomain, pick any
   name. (The bot's address will be `ai-news-bot.<that-name>.workers.dev`.)
3. **Account ID:** on the **Workers & Pages** page it's shown on the right ("Account ID"); copy it.
4. **API token:** click your profile icon → **My Profile → API Tokens → Create Token**, choose the
   **Edit Cloudflare Workers** template, set **Account Resources** to your account, then **Continue to summary →
   Create Token**. Copy the token (it's shown once).
5. In GitHub (**Settings → Secrets and variables → Actions → New repository secret**), add:
   - `CLOUDFLARE_API_TOKEN`: the token from step 4
   - `CLOUDFLARE_ACCOUNT_ID`: the ID from step 3
   - Optional, for `/news`: `BOT_GITHUB_TOKEN`, a fine-grained GitHub token (github.com → Settings → Developer
     settings → Fine-grained tokens) limited to this repository with **Actions: Read and write**.
6. Run **Actions → Deploy Telegram bot (Cloudflare) → Run workflow → deploy**. When it's green, send `/help` to
   your bot: the reply should say "(instant replies)".

To go back to hourly replies, run the same workflow with **remove**.

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
  1,500 (briefs about 240, hourly alerts about 720, Buzz about 480). A public repository has no limit.
- **GitHub pauses scheduled workflows after 60 days without activity** in public repositories. The agent commits its
  memory after runs, which counts as activity. If it ever stops, re-enable it in the Actions tab.
- **Always open the source before you post.** The AI summarises; it can misread. The *Official / Report / Rumor*
  label tells you how much to trust each story.

## Files

| Path | What it is |
|---|---|
| `sources.yaml` | where the agent looks |
| `config.yaml` | companies, keywords, AI providers, delivery |
| `agent/` | the code: `sources.py` (fetchers), `digest.py` (AI prompt and formatting), `accuracy.py` (articles, source counts, blocklist), `factcheck.py` (second-AI check), `builders.py` (Builder picks), `buzz.py` (3-hourly Buzz), `weekly.py` (recap), `freemodels.py` (`/free`), `commands.py` (Telegram commands), `botpush.py` (feeds the instant bot), `llm.py` (providers), `deliver.py` (Telegram/email), `__main__.py` (the runs) |
| `bot/` | the instant Telegram bot for Cloudflare Workers, its tests and deploy script |
| `state/` | the agent's memory of what it already sent (saved by the workflows) |
| `digests/` | archive of every brief |
| `.github/workflows/` | the schedules |
