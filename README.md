# AI News Agent

A free agent that follows four AI companies, **Anthropic (Claude), OpenAI (GPT), xAI (Grok) and Zhipu / Z.ai (GLM)**,
and sends you a summary on **Telegram** and/or **email**.

- **Every 6 hours:** reads about 65 sources (official blogs and changelogs, leak trackers, Hacker News, Reddit,
  newsletters, YouTube news channels and interview podcasts, the press), keeps only what's new and about your four
  companies, and has a free AI model write a brief: grouped by company, labelled *Official / Report / Rumor*, with
  links and post ideas. Interviews (a CEO or researcher on a podcast, an AMA on Reddit) are marked 🎙.
- **🚨 New AI model alerts (hourly):** watches the *official* release sources of **Anthropic (Claude), OpenAI (GPT),
  Moonshot AI (Kimi), Alibaba (Qwen), xAI (Grok) and Google (Gemini)**: announcement posts, API release notes,
  model catalogs and their own Hugging Face / GitHub organisations. When a lab officially releases a new model you get
  a verified Telegram alert that compares it with its predecessor, and a delivery receipt once Telegram confirms it.
  See [Model release alerts](#model-release-alerts).
- **Every hour:** checks the official sources plus the early-signal sources (TestingCatalog leaks, new and stealth
  models on OpenRouter, new model listings on models.dev) and the free-model sources (new free models on OpenRouter
  and in OpenCode), and pings you on Telegram as soon as something new appears (no AI, so it's fast and always works).
- **New free models:** the brief has a "🆓 New free models you can use" section for models that just became free on
  OpenRouter or in OpenCode, from any company.
- **Every 3 hours:** "🔥 Buzz", something to talk about: the most talked-about things on **GitHub, Hugging Face,
  Reddit and YouTube** right now. Trending repos (AI first, but a non-AI repo that's blowing up gets in too), new
  models and upgrades (extra focus on Claude, OpenAI, GLM, Kimi, MiniMax, Grok, DeepSeek, Qwen, Gemini/Gemma and
  Llama), uncensored models, trending Spaces and papers, the Hugging Face blog, today's top posts in the AI
  subreddits, the most-watched new videos from AI channels, plus news searches for free/limited-time offers and
  censorship. Each pick says what it is, why people are talking, and a line you could post.
- **🎙 Interviews with people who work in AI:** every Buzz puts 2-3 of the most-watched or most-discussed interviews
  first (founders, CEOs, researchers, engineers and investors on Dwarkesh Patel, Lex Fridman, No Priors, Latent
  Space, Bg2, All-In, 20VC and more, plus Reddit AMAs), with who the guest is and what they said.
- **Every morning:** "🛠 Builder picks", the most useful trending AI tools and repos (GitHub Trending, fast-rising
  new repos, Hugging Face, Show HN), each with what it does and who it helps.
- **Every Saturday (about 3 pm Lagos time):** the **Weekly Model Intelligence Report** on the week's new models from
  the six labs: comparisons with their predecessors, updates, the competitive landscape, company-reported and
  independent (LMArena) benchmarks, and ranked content ideas (`/weeklymodels`).
  See [Weekly Model Intelligence Report](#weekly-model-intelligence-report).
- **Every Sunday:** a weekly recap of the biggest stories, with content opportunities (`/weekly`).
- **🚀 Every 3 hours: AI skills & open-source discoveries.** A discovery engine scouts GitHub, Hugging Face, the
  official MCP registry, GitLab and Codeberg for practical, free resources (agent frameworks, Claude Code / Codex /
  Gemini skills, MCP servers, prompt engineering, app templates, automation, hackathon starters, creator and developer
  tools), checks each one (licence, cost, maintenance, documentation, security) and sends only new, useful ones as one
  curated batch, never the same resource twice (`/discoveries`).
  See [AI Skills & Open-Source Discovery](#ai-skills--open-source-discovery).
- **🔑 Model Access Intelligence (`/modelaccess`).** Where any model (Claude, GPT, Gemini, Grok, Llama, DeepSeek, Qwen,
  Mistral, Kimi, GLM, MiniMax and the rest) can be used for free (free tiers with limits, credits, free plans) or
  cheaply, across official APIs, third-party providers, coding platforms and apps, refreshed every 6 hours, with
  free-access alerts and a Saturday Weekly Model Access Report. Separate from `/free`.
  See [Model Access Intelligence](#model-access-intelligence).
- **Organised like a research assistant:** every message uses the same ten sections, four priorities (🔴 P0 to ⚪ P3)
  and the same card layout, explains important developments in plain language, suggests news/education/analysis
  video ideas, and never sends the same story twice. See [How Telegram messages are organised](#how-telegram-messages-are-organised).
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
  the GitHub topics, the subreddits (`buzz.reddit`), the YouTube channels (`buzz.youtube`, add any channel by its
  @handle) and the offer/censorship news searches are in `config.yaml` → `buzz`. Run it by hand with
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

## How Telegram messages are organised

Everything the agent sends uses one structure (`agent/messages.py`), so a release alert, the 6-hour brief, Buzz,
Builder picks and the weekly report all read the same way.

**Ten sections**, always in this order, and only the ones with something in them:

1. 🚨 NEW MODEL RELEASES · 2. 🔄 MODEL UPDATES AND IMPROVEMENTS · 3. 🧠 AI RESEARCH AND BENCHMARKS ·
4. 📰 AI INDUSTRY NEWS · 5. 🔥 TRENDING AI DISCUSSIONS · 6. 🆓 FREE AI MODELS AND TOOLS ·
7. 🛠 NEW AI TOOLS AND PROJECTS · 8. 🎙 AI PODCASTS AND INTERVIEWS · 9. 💡 CONTENT OPPORTUNITIES ·
10. ⚙️ AGENT HEALTH AND SYSTEM NOTIFICATIONS

Only an official release counts as a "new model release": a leak or press report about an upcoming model is filed
under industry news with a 👀 label.

**Four priorities** decide when you hear about something and how it arrives:

| Priority | What | When | Sound |
|---|---|---|---|
| 🔴 P0 Critical | confirmed flagship model releases, major developments | at once (release monitor, every alerts run) | yes |
| 🟠 P1 High | other confirmed new models, significant upgrades, important announcements, major research | at once | yes |
| 🔵 P2 Normal | industry news, benchmarks, leaks, official page changes, notable discussions | at once in instant alerts, otherwise in the digest | silent |
| ⚪ P3 Discovery | free models, repositories, experiments, tools | the digest, Buzz and Builder picks | silent |

A model counts as a flagship when its id matches the company's `flagship_patterns` in `releases.yaml`, or the
official text calls it "flagship", "most capable" or "frontier". Change what is sent at once with
`config.yaml → alerts.min_priority` and what arrives silently with `config.yaml → telegram.silent_from`.

**Every item is a card** with the same fields in the same order: headline · priority, verification and date ·
company (and the original speaker or publication for interviews, research and discussions) · what happened ·
why it matters · technical details · sources. Messages start with a priority summary (e.g. `🔴 1 P0 · 🔵 4 P2`).

**Explained.** For important stories (P0/P1 in the brief, every confirmed release) a collapsed "📘 Explained" block
answers: what was introduced, what it actually does, what problem it solves, how it differs from before, who
benefits, and why a creator or developer should care. Tap it to open; it stays out of the way otherwise.

**💡 Content opportunities.** For the most important story of a brief, Buzz, the weekly report and every confirmed
release, three ideas: **A. NEWS** (a timely video), **B. EDUCATION** (explaining the technology) and **C. ANALYSIS**
(a comparison, test or opinion), each with a suggested title, the main angle, why it is interesting and what still
needs research or testing. Nothing is created or published: you decide.

**Accuracy.** The AI only adds explanations and ideas on top of the sources: any sentence with a number that appears
in none of the sources is dropped, and a "new model release" needs an official source.

**Long reports** are split into several messages in order, never in the middle of a card or an HTML tag; a section
that continues gets its heading again, marked "(continued)", and each part is numbered (1/3, 2/3, …).

**No repeats.** Besides each run's own memory, `state/sent.json` records what was actually delivered (links,
headlines, model identities) for 14 days, across all runs:
- a model release delivered by the release monitor is not repeated as an instant alert, a brief story or a Buzz pick
  (the brief's footer lists it as "already sent as alerts");
- several sources reporting the same thing in one run become one card with several links, and the same story with a
  reworded headline in a later run is recognised and skipped ("Grok 5 …" and "Grok 6 …" are kept apart);
- minor edits to documentation pages (no price, limit, model or deprecation change) are not sent at all;
- when a story really develops, it comes back once, marked 🔄 update, saying what is new (release alerts get a
  separate 🔄 UPDATE message).

## Ask your agent

Send these to your bot in Telegram (they also appear in the bot's **/** menu):

| Command | What you get |
|---|---|
| `/latest` | The most recent news brief again |
| `/buzz` | The latest Buzz: what's hot on GitHub, Hugging Face, Reddit and YouTube, with interviews |
| `/repos` | Today's Builder picks |
| `/discoveries` | The latest AI skills & open-source discovery batch |
| `/weekly` | The latest weekly news recap |
| `/weeklymodels` | The latest Weekly Model Intelligence Report |
| `/ask your question` | An answer from this week's briefs, e.g. `/ask what did OpenAI change this week?` |
| `/models` | Recent confirmed new model releases from the six labs |
| `/releases` | The full release history (new models, upgrades, availability, reviews) with verification and delivery status |
| `/compare model A vs model B` | Two models side by side from the official catalogs (context, output limit, prices, latency, inputs, availability), e.g. `/compare Claude Opus 5.5 vs GPT-6.1 Terra` |
| `/free` | Every model you can use for free right now on OpenRouter and in OpenCode, newest first (🆕 = added in the last 14 days) |
| `/modelaccess` | Where models can be used for free or cheaply. Also `/modelaccess free`, `cheapest`, `leaderboard`, `weekly`, `/modelaccess Claude` (a company), `/modelaccess Claude fable 5.2` or `/modelaccess deepseek v4 flash` (one model), `/modelaccess compare kimi k3` (every provider's price), `/modelaccess alternatives claude fable 5.2` |
| `/sources` | Every official source the release monitor watches, with ✅ or ⚠️ failing |
| `/health` | Latest run of every workflow, how often the hourly alerts really ran, failing sources, alert delivery |
| `/news` | Run a fresh news check now (instant bot only) |
| `/help` | The list of commands |

`/compare` uses official specs only (no benchmarks); specs for models that only have a name in a catalog are filled
in a few pages per release check. `/weekly` is available after the next Sunday report.

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

## Model release alerts

A dedicated monitor (`agent/releases/`, configured in `releases.yaml`) for new models from **Anthropic, OpenAI,
Moonshot AI, Alibaba, xAI and Google**. It runs inside every "Instant alerts" run and on demand (**Actions → Model
releases → Run workflow**).

**Where it looks (all official; every endpoint was fetched and parsed on 2026-11-16, except the GitHub organisation
lists, which use the same GitHub API your Buzz run already uses for these organisations):**

| Company | Announcements | API release notes | Model catalog (ids, specs, prices) | Release repos |
|---|---|---|---|---|
| Anthropic | anthropic.com/news (incl. launch posts like /claude-opus-5-5) | Claude Platform release-notes feed | Claude models overview + deprecations (Markdown) | none (no open weights) |
| OpenAI | openai.com/news RSS | API changelog (Markdown, tagged `Feature · Model: …`) | developers.openai.com model catalog + per-model pages | Hugging Face `openai`, GitHub `openai` |
| xAI | x.ai/news | docs.x.ai release notes | docs.x.ai models & pricing | Hugging Face `xai-org`, GitHub `xai-org` |
| Google | DeepMind blog, Gemini blog, Developers Blog (RSS) | Gemini API changelog + Gemini app release notes | Gemini API models (+ the official model-list API if `GEMINI_API_KEY` is set) | Hugging Face `google` |
| Moonshot AI | kimi.com/blog | (the platform changelog stopped in 2025) | platform.kimi.ai model list | Hugging Face `moonshotai`, GitHub `MoonshotAI` |
| Alibaba | (qwen.ai only renders in a browser; its old blog feed stopped in 2025) | | Alibaba Cloud Model Studio "newly released models" (Qwen only) | Hugging Face `Qwen`, GitHub `QwenLM` |

OpenRouter, models.dev and TestingCatalog are read too, but only as **early signals**: they can never confirm a
release. A source is only treated as official when its address is on the company's own domains (checked by
`python -m agent check-releases`, which also tests every source).

**How a change is classified.** Everything the sources report is put in one of six categories:
A `NEW_MODEL_RELEASE` · B `MAJOR_MODEL_UPGRADE` · C `MODEL_AVAILABILITY` · D `DOCUMENTATION_OR_PRICING_CHANGE` ·
E `PRODUCT_OR_FEATURE_ANNOUNCEMENT` · F `UNCONFIRMED_EARLY_SIGNAL`, and gets a verification status:
`OFFICIAL CONFIRMED`, `OFFICIAL UPDATE`, `UNCONFIRMED`, `REJECTED AS DUPLICATE` or `REQUIRES MANUAL REVIEW`.
The rules are deterministic Python (`agent/releases/classify.py`): a model family/version/tier never seen in the
official catalogs, introduced by an official post, changelog entry, catalog listing or repository, is a new model;
a new dated snapshot or "-preview"/"-highspeed" variant of a known model is an upgrade; GA, new regions, apps or
tiers are availability; price, limit and deprecation edits are documentation changes. Only `OFFICIAL CONFIRMED` new
models get the full 🚨 alert; upgrades and availability get shorter ⬆️/🟢 alerts; documentation, feature and early-signal
items are saved but not sent (switch any of them on in `releases.yaml → settings.notify`). If the official text can't
be read, or the agent finds conflicting evidence, you get a short 🔎 "requires manual review" note instead.

**Verification and comparison.** For a confirmed launch the agent reads the official announcement, release note and
model page, takes the announcement and availability dates from the sources themselves (both are shown separately;
"Not stated" when a source doesn't say), and finds the previous model: first the company's own words ("an upgrade
over Claude Sonnet 5", "migration from Gemini 3.8 Flash"), otherwise the model lineage in the official catalog (same
family and tier, previous version; xAI's 4.20 correctly sorts before 4.3). It compares context window, output limit,
prices, latency, modalities and availability from the official specs, and, with an AI key, reads the announcement for
benchmarks and claims. Every AI statement must quote the official text verbatim and every number must appear in the
sources, otherwise it is thrown away. Company-reported benchmarks, qualitative claims and the AI's interpretation are
labelled separately; independent benchmarks are listed as not collected. Without any AI key the alert still goes out
with the facts from the official pages.

**Delivery confirmation.** Alerts go through an outbox in `state/releases.json`: `PENDING` → `SENT` only when
Telegram itself answers `"ok": true` with a message id for every part, which is saved with the time. A failed send
becomes `RETRY_PENDING` and is retried on the next run, re-sending only the parts Telegram didn't confirm; after 6
failed runs it is `FAILED` (kept in the history). Once confirmed, the alert's "DELIVERY" line is updated to show the
message id and a short ✅ delivery receipt follows. Verification and delivery status are stored separately.

**Release history.** `state/releases.json` keeps every release with company, model, previous model, classification,
announcement and availability dates, official source, verification status, comparison summary, first-detected and
last-updated times, Telegram delivery status and message ids. It is never pruned. Confirmed alerts are also saved as
Markdown in `releases/`. The first run (and the first run of any newly added source) only learns what is already
published and sends nothing.

**How fast, honestly.** This is scheduled polling: none of these labs offers a push notification for new models, so
real-time detection isn't possible on this setup. The check itself takes seconds. The delay is the time until the next
run plus the minutes a lab takes to update its pages:
- **Default (no setup):** every "Instant alerts" run, scheduled hourly. GitHub skips many scheduled runs when it is
  busy; this repository's hourly alerts have actually run every **4-9 hours**. Expect alerts within hours, not minutes.
- **Dependable hourly (recommended, free):** let your Cloudflare bot start the check on time. Add the
  `BOT_GITHUB_TOKEN` secret (see "Instant replies"), add a repository **variable** (Settings → Secrets and variables →
  Actions → Variables) named `RELEASE_CHECK_CRON` with the value `7 * * * *`, and run **Deploy Telegram bot →
  deploy** again. Expected delay: under about 65 minutes. Cost: up to about 720 extra GitHub Actions minutes a month
  (each run is billed as at least one minute), which fits the free 2,000 minutes of a private repository together
  with the other runs at their real frequency. A public repository has unlimited minutes, so `*/15 * * * *` (≈15
  minutes) would be possible there.

**Secrets.** Nothing new is required: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` for the alerts, and your existing
free AI keys (optional) for reading and comparing announcements. `GITHUB_TOKEN` is provided by GitHub automatically
(used to list the labs' GitHub repositories). Optional: `ANTHROPIC_API_KEY` adds Anthropic's Models API,
`GEMINI_API_KEY` adds Google's official model list, and `BOT_GITHUB_TOKEN` + the `RELEASE_CHECK_CRON` variable enable
the dependable trigger above.

**Commands.** `python -m agent releases` (add `--dry-run` to print without sending or saving, `--bootstrap` to learn
the sources again without alerts) and `python -m agent check-releases`. In the **Model releases** workflow these are
the modes `releases`, `preview`, `bootstrap` and `check`.

**Known limits.** qwen.ai can't be read without a browser, so Qwen launches are confirmed through Alibaba Cloud's
docs and Qwen's own repositories. xAI's release notes give only the month. If Telegram accepts a message but the
answer is lost in a network error, that part is sent again on the next run (Telegram offers no way to check).

## Weekly Model Intelligence Report

Every Saturday (scheduled 13:53 UTC, 14:53 in Lagos, so that with GitHub's usual start delay it arrives around
3 pm Lagos time; workflow `weekly-models.yml`), the agent sends a report on the AI models Anthropic, OpenAI,
Moonshot AI, Alibaba, xAI and Google officially released in the last seven days
(`agent/releases/weekly.py`, `python -m agent weekly-models`). It is built from the release monitor's own history
(`state/releases.json`), not from the news briefs.

**Sections**
1. **🚨 New model releases**: every confirmed release with company, model, announcement date, availability date,
   main capabilities, official announcement link and API/product availability.
2. **↔️ Model-to-model comparisons**: each new model against its predecessor (the one the company itself named, or
   the previous version in its catalog): reasoning, coding, multimodal, context window, speed, pricing and benchmark
   results. Rows without evidence are listed as "Not established" instead of being guessed.
3. **🔄 Important model updates**: upgrades, availability changes, pricing changes, retirements and API changes,
   kept apart from new models; unconfirmed candidates are listed separately and never counted as releases.
4. **🧭 Competitive landscape**: activity per company and evidence-based answers to: which companies introduced
   significant improvements, which capabilities are advancing, where competitors converge or differ, and which
   limitations remain; plus where each company's best model stands on LMArena.
5. **📈 Benchmark intelligence**: company-reported results (from the official announcements, labelled as such) and
   independent results from **LMArena** (the official `lmarena-ai/leaderboard-dataset` on Hugging Face, updated daily:
   crowdsourced blind pairwise votes, Elo-style ratings with 95% confidence intervals, for general text, coding, math
   and vision). Scores from different evaluations are never compared with each other; within LMArena, a difference
   is only called real when the confidence intervals don't overlap.
6. **💡 Content opportunities**: three video topics, three X posts, one model comparison and one deeper educational
   topic, each scored 1-5 for relevance, evidence quality (computed from the evidence: official confirmation,
   specs, benchmarks) and educational value, and ranked by the total.

**Counting rules.** A release is counted once, in the first Saturday report after it was confirmed: the report
records which releases it covered, so a manual re-run or the next week never counts them again. A model announced
earlier but only detected this week is included and marked ⏱ "detected late". Two records for the same model
(e.g. a launch that arrived through several sources) count as one.

**Without AI keys** the report is still complete: comparisons, updates, benchmarks and the landscape answers come
straight from the evidence, and content ideas are generated from the strongest evidence. With a key, an AI writes
the landscape answers and the content ideas from the same facts; any sentence with a number that isn't in the
facts is thrown away.

**Archive and commands.** Every report is saved as Markdown in `releases/weekly/YYYY-MM-DD.md`; `/weeklymodels`
resends the latest one. Run it by hand with **Actions → Weekly model report → Run workflow** and choose `models`
(send now) or `preview` (print in the log, nothing sent or saved). The Sunday news recap is a separate workflow and
works as before. Independent benchmark sources are configured in `releases.yaml → independent_benchmarks`.

## AI Skills & Open-Source Discovery

A discovery engine for resources you can actually use to build agents, apps, prompts, workflows, hackathon projects
and videos (`agent/discovery/`, configured in `discovery.yaml`, workflow `discovery.yml`, every 3 hours at :29).
It is not a news collector: it only recommends things it has checked.

**From search to Telegram**
1. **Deliver first:** a batch from an earlier run that Telegram hasn't confirmed is sent before anything new (and no
   new batch is stacked on top of it).
2. **Search:** 32 targeted GitHub searches (by topic, tool, framework, ecosystem and problem; `new` = created in the
   last 60 days, `active` = pushed in the last 14 days, each with a minimum of stars), rotated so each runs about once
   a day; new repositories of official AI organisations (Anthropic, OpenAI, Google Gemini/DeepMind, Hugging Face,
   MCP, LangChain, LlamaIndex, CrewAI, Microsoft); GitHub Trending; Hugging Face trending and searched Spaces,
   trending models and datasets; recently updated servers in the official MCP registry (registry.modelcontextprotocol.io,
   matched to their GitHub repository); GitLab and Codeberg once a day. GitHub calls are budgeted (120 per run) with
   rate-limit handling and backoff; repositories found elsewhere are looked up several at a time.
3. **Filter:** one entry per resource; anything already delivered, queued, waiting or recently rejected is skipped.
4. **Evaluate** (deterministic Python; READMEs are read as untrusted text, never executed or obeyed):
   - is it really an AI resource (its own words, not just its tags), and which of the eight categories: agent building,
     skills and instructions, prompt engineering, app development, automation and workflows, hackathon and personal
     projects, creator tools, developer productivity;
   - **security:** requests for secrets, hidden decode-and-run code, exposed API keys, prompt injection aimed at AI
     agents, miners, password-protected .exe installers → rejected; `curl … | sh`, sudo or `rm -rf` steps → kept with a
     ⚠️ warning and fewer points;
   - **licence and cost:** FREE AND OPEN SOURCE · FREE TO USE, CLOSED SOURCE · FREE TIER AVAILABLE · OPEN SOURCE WITH
     PAID OPTIONAL SERVICES · PAID OR RESTRICTED · LICENCE UNCLEAR (a public repository is never called free just
     because it is public), plus what it needs: paid API, subscription, cloud infrastructure, GPU, paid hosting,
     commercial licence;
   - **quality score (0-100):** category fit, documentation (install, usage, examples), maintenance (last update,
     archived), accessibility (licence/cost), practical implementation (real install commands and code), and stars,
     forks, likes, downloads and contributors as a capped supporting signal; plus a boost when it fits your work
     (`discovery.yaml → profile`). Link lists without an implementation, abandoned or thin repositories lose points
     and say why.
   - **priority:** 🔴 P0 exceptional (85+, and only when open, well documented, active and relevant to your work) ·
     🟠 P1 highly useful (72+) · 🔵 P2 worth exploring (58+) · ⚪ P3 interesting (45+); below 45 it is never sent.
5. **Pool:** resources that pass wait (up to 10 days) for a batch, so good finds are collected between runs.
6. **Updates:** delivered GitHub repositories are re-checked weekly; only a meaningful new release (a new major
   version, or a minor version whose notes describe real new features) is sent, as "what changed", never the original
   description again. Patch releases, routine commits and documentation changes are ignored.
7. **Batch:** the best of the pool (8 at most, 3 per section, best first, each resource once); no message at all if
   nothing new reaches P2.
8. **Explain:** one AI call writes what each resource actually does, the problem it solves, why it matters, how to use
   it, and how it connects to your projects; README text is passed as quoted untrusted data, and sentences with
   numbers that aren't in the resource's own data are dropped. Without AI keys the batch still goes out, worded from
   the evaluation.
9. **Send** through the same Telegram outbox as release alerts: a resource becomes "delivered" only when Telegram
   confirms the message (message ids saved); a failed batch is retried next run, only the unconfirmed parts.
10. **Remember:** `state/discovery.json` keeps every resource (name, canonical URL, platform, owner, repository id,
    categories, first discovered, last checked, score, licence class, delivery status, Telegram message ids, last
    notification, latest version) and is committed after every run. Delivered resources are kept forever; Buzz and
    Builder picks also skip repositories the discovery engine has sent (`state/sent.json`).

**The batch** starts with 🚀 AI SKILLS & OPEN-SOURCE DISCOVERIES (date, discovery period, number of new
discoveries), then only the sections with something in them: 🧠 AI AGENT BUILDING, 🛠 AI SKILLS AND PROMPTS,
⚡ AI AUTOMATION AND WORKFLOWS, 🏗 HACKATHON AND PROJECT RESOURCES, 🎬 AI CREATOR RESOURCES, 💻 DEVELOPER PRODUCTIVITY,
each card with the fields for its kind (e.g. developer, what it does, why it matters, practical application,
compatibility, cost, licence and link for agent resources; installation and where it plugs in for skills), its
priority and score, stars/forks/last update, warnings, and "How this could help your …" when it fits your work.
It ends with 🏆 BEST DISCOVERY: why it ranked first, the problem it solves and what you could build with it.
`/discoveries` resends the latest batch.

**Configuration** (`discovery.yaml`): `settings` (batch size, minimum score, pool time, GitHub request budget,
queries per run, excluded topics), `priorities`, `categories` (keywords), `profile` (your projects and what connects to
them), and `sources` (searches, organisations, platforms; each can be switched off). **Secrets:** none new.
`GITHUB_TOKEN` is provided automatically by GitHub Actions (higher search limits); `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` for delivery; your existing free AI keys (optional) for the explanations.

**Limits:** Codeberg's search is often throttled by Codeberg itself (it is tried daily and skipped when it refuses);
Papers with Code closed in 2025 and Hugging Face papers have no code links, so research implementations come through
GitHub searches; the extra workflow uses about 8 short runs a day (roughly 250-450 GitHub Actions minutes a month,
inside the free allowance of a private repository with the other runs at their real frequency).

## Model Access Intelligence

`/modelaccess` answers "where can I use this model for free, or cheapest?" (`agent/modelaccess/`, configured in
`modelaccess.yaml`). It is independent of `/free`: different data, different replies, and every reply begins with
**[MODEL ACCESS INTELLIGENCE]**.

**What it finds.** Models are discovered from the provider catalogs, not from a fixed list: models.dev (about 320
providers' official and third-party price lists), OpenRouter's live catalog and its per-provider breakdowns, and
Google's Gemini API pricing page. Each model is grouped under its maker (OpenAI, Anthropic, Google, xAI, Meta,
DeepSeek, Alibaba/Qwen, Mistral, Microsoft, NVIDIA, Zhipu, Moonshot, MiniMax, Cohere, AI21 and any other author the
catalogs name) and tagged general, reasoning, coding, multimodal, image, video, audio, embedding, agentic or small.
Coding platforms and apps (GitHub Copilot Free, Google AI Studio, Claude.ai and ChatGPT free plans, Cursor, Windsurf,
Arena, NVIDIA's API catalog, ...) are listed in `modelaccess.yaml` with their source page.

**Statuses.** Every offer and every model gets one: `FREE` (open weights you run yourself), `LIMITED_FREE` (a free
tier with limits; never described as unlimited), `FREE_CREDITS`, `LOW_COST` (blended price at or under $1 per 1M
tokens), `PAID_ONLY` (including "included in a paid subscription"), `UNVERIFIED`.

**How it avoids false claims.**
- A $0 price only counts as free for providers whose free tier is confirmed (OpenRouter's `:free` models, the Gemini
  API Free tier, Z.ai's Flash models, OpenCode/Vercel free models, ModelScope, SiliconFlow China). Elsewhere a $0
  listing is shown as UNVERIFIED (it is often a gap in the catalog, or per-request pricing), and a $0 price in a
  coding or token plan means "included in the plan".
- Google's own pricing page overrides the catalog (e.g. Lyria is listed at $0 but has no Free tier: UNVERIFIED).
- Published terms are re-checked on each refresh: OpenRouter's free limits (20 requests/minute; 50 a day, or 1,000 a
  day after $10 of credits), GitHub Copilot Free (AI credit allowance, 2,000 completions a month, auto model
  selection only), GitHub Models (confirmed retired on 30 July 2026: shown as discontinued). An entry whose claim
  disappears from its page drops back to UNVERIFIED.
- Anything not stated by a source is shown as "not stated" (card, geography, commercial use, ...), never guessed.
- Community claims (Reddit leads, and the X posts it was seeded with: "unlimited" DeepSeek V4 Flash on OMO dev,
  Gemini 4 Argon free on Arena, Zonos3-Mini voice) are always labelled UNVERIFIED. Uncensored/abliterated models
  and providers that advertise removing safety are excluded.
- "Cheapest", "best value", "best for agents" and the leaderboard only use the official APIs and established
  providers (`vetted` in `modelaccess.yaml`); small resellers still appear in `/modelaccess compare`, labelled
  "unvetted reseller".

**Per model** (`/modelaccess <model>`): status, best free option with every term (platform, exact model id, access
type, registration, card, daily/monthly limits, token quota, rate limits, context, geography, commercial use,
permanent or temporary, link, verification date), cheapest verified provider, official price, best value for
developers (keeps the context window and tool calls), best for agents (cached-input cost), best for heavy usage
(output-heavy), subscriptions that include it (priced separately), open weights. Prices are USD per 1M tokens:
input, output and cached input; "blended" = 3 parts input to 1 part output.

**Leaderboard** (`/modelaccess leaderboard`): best completely free, best free API providers, coding, reasoning,
agents, cheapest premium access, best overall value. Ranked on a transparent capability score (the maker's price
tier, reasoning, tool calling, context, recency, how many providers host it) against price, never on how many models
a provider lists.

**Database.** `state/modelaccess.json` (one record per line, committed like the rest of the memory): models, offers
(first seen, last seen, last verified, active), platform checks, price/quota/status change history, alert history,
community leads and the alert outbox. A refresh merges into it; an offer is only marked gone after its catalog loaded
fine twice without it, and a catalog that fails to load keeps its last good data (the reply footer says so).

**Schedule (no new scheduler).** Refresh: a step of the 6-hourly news digest run (`python -m agent model-access`,
about 15 seconds; a failure never affects the brief). Free-access alerts go out from that step: paid → free, new
free offer, new model with free access, a new provider at least 15% cheaper for a premium model, price drops of 25%
or more, changed free limits, free access that ended, new or discontinued platforms. Each alert has the model,
provider, what changed, before and now, quota or pricing, link, source and verification date; it is recorded as sent
only after Telegram confirms it and is never sent twice. **Weekly Model Access Report:** Saturdays, right after the
Weekly Model Intelligence Report (same workflow), opening with **BEST MODEL ACCESS OPPORTUNITIES THIS WEEK**, then new
free access, price and limit changes, ended free access, the leaderboard and unverified leads (`/modelaccess weekly`).

**Bots.** The instant (Cloudflare) bot gets the ready-made replies after each refresh (only the pieces that changed:
18 small storage keys), so it answers in seconds with the same routing as the Python side. Redeploy the bot once
(Actions → Deploy Telegram bot (Cloudflare) → Run workflow) so it learns `/modelaccess`. Without the instant bot, the hourly run answers.

**Run it by hand:** Actions → AI news digest → Run workflow → `model-access`, or `python -m agent model-access
--dry-run` (prints, saves nothing). **Secrets:** none new (it only reads public catalogs; Telegram secrets for alerts).

## Customising

- **Model release sources:** `releases.yaml`, one block per company with its official hosts, model-name patterns and
  sources (each with how it was verified). Run `python -m agent check-releases` after editing it.
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
| `releases.yaml` | the model release monitor: official sources and settings for the six labs |
| `discovery.yaml` | the AI skills & open-source discovery engine: searches, categories, scoring, your projects |
| `modelaccess.yaml` | Model Access Intelligence: companies, how $0 prices are read, vetted providers, platforms and their checks |
| `config.yaml` | companies, keywords, AI providers, delivery |
| `agent/` | the code: `messages.py` (sections, priorities, cards, message splitting), `ledger.py` (what was already sent), `alerts.py` (instant alerts), `health.py` (/health), `sources.py` (fetchers), `digest.py` (AI prompt and formatting), `accuracy.py` (articles, source counts, blocklist), `factcheck.py` (second-AI check), `builders.py` (Builder picks), `buzz.py` (3-hourly Buzz: GitHub, Hugging Face, Reddit, YouTube), `weekly.py` (recap), `freemodels.py` (`/free`), `commands.py` (Telegram commands), `botpush.py` (feeds the instant bot), `llm.py` (providers), `deliver.py` (Telegram/email), `__main__.py` (the runs), `modelaccess/` (Model Access Intelligence, `/modelaccess`: `sources.py`, `normalize.py`, `db.py`, `analyze.py`, `alerts.py`, `render.py`, `respond.py`, `pipeline.py`), `discovery/` (AI skills & open-source discovery: `sources.py`, `evaluate.py`, `explain.py`, `render.py`, `pipeline.py`, `store.py`), `releases/` (model release monitor: `parsers.py`, `classify.py`, `analyze.py`, `render.py`, `notify.py`, `pipeline.py`; `weekly.py` the Weekly Model Intelligence Report; `benchmarks.py` LMArena) |
| `bot/` | the instant Telegram bot for Cloudflare Workers, its tests and deploy script |
| `state/` | the agent's memory of what it already sent (saved by the workflows) |
| `digests/` | archive of every brief |
| `releases/` | archive of every confirmed model release alert, and of every Weekly Model Intelligence Report (`releases/weekly/`) |
| `.github/workflows/` | the schedules |
