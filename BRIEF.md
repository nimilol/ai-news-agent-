# Brief

## Original prompt (word for word)

> question
>
> i want to be a creator who shares early info about ai companies (model updates and releases, news about what is happening to ai particularly model or what a particular ai company changes) basically an ai journalist
>
> but i don’t know were to get good and early news from
>
> i want to focus this my journalism approach to 4 models only which includes
>
> claude
> gpt (openai)
> Xai/grok
> and glm
>
> i don’t know official website, newsletters or articles that will bring this information to me
>
> help me list major sources and also you can add sources like ai explained and more
>
> and is it possible for me to build an ai agent that will read through all this newsletters and send me a summary of what happened
>
> a change or more so i can be up to date
>
> i knows an agent like this will need an api key but i can’t afford that right now
>
> is there a way i can use a free model to help me archive this
>
> i want to make this agent a self sustainable and working agent till i have money to use claude api
>
> it should be able to send me the summary either in whatsapp, telegram or email
>
> this build is to help reduce my work and is this possible to run free or must i be able to pay for api to have the agent do the research and then send a summary to me
>
> the agent can just run in like 6 hour intervals so not to be active around
>
> what do you think about this
>
> is this possible

Follow-up:

> we can leave X api and whatsapp
>
> do you need me to get any other things before you build? or you can start building plus i want to move what we building into its own repo

## Decisions

- **Scope:** four companies: Anthropic/Claude, OpenAI/GPT, xAI/Grok, Zhipu/Z.ai/GLM. No X API and no WhatsApp
  (owner's call). Delivery by Telegram and email.
- **Runs on GitHub Actions** (free, no server): AI digest every 6 hours, plus an instant no-AI alert every
  hour for official sources only (hourly because the repo is private; free minutes are capped at 2,000/month), because a 6-hour gap is too slow for "early news".
- **Free AI with fallbacks:** Gemini → Groq → OpenRouter (auto-picks a free model) → Z.ai GLM Flash. Each provider lists several models so a retired model falls through. If every provider fails,
  the brief is sent as plain headlines, so a run is never lost.
- **Claude-ready:** Claude is first in the order and is used as soon as `ANTHROPIC_API_KEY` is added, via the
  official Anthropic SDK (default model `claude-opus-5-5`, low effort, server-side refusal fallback on).
- **The AI returns JSON, and the code renders it.** Every story must cite item numbers from the input, and links come
  from those items. The AI never writes links, so it can't invent sources.
- **Labels:** each story is tagged Official / Report / Rumor and given an importance from 1 to 5, to keep the
  creator's posts honest about what's confirmed.
- **Memory:** `state/*.json`, committed by the workflows. Items are remembered for 30 days after they were last seen.
  The first read of a source only reports the last 12 hours.
- **Sources:** 42 to start (Reddit is one combined source), in `sources.yaml`: official blogs, changelogs and release notes for each company,
  Hugging Face orgs, OpenRouter's model list (catches stealth models), TestingCatalog, Hacker News, Reddit,
  newsletters (Simon Willison, Latent Space/AINews, Interconnects, Import AI, TLDR AI, Ben's Bites), YouTube
  (AI Explained, Matthew Berman, Wes Roth, Sam Witteveen), Google News and tech press. AINews moved from
  news.smol.ai to Latent Space, so the Latent Space feed is used.
- **Broken sources never stop a run.** A source failing 3 runs in a row is named in the brief's footer.

## Changes

- **2026-10-02, after the first live source check on GitHub Actions (41/47 working):** Reddit returned
  "429 Too Many Requests" after the first one or two feeds (even spaced 4 seconds apart), so all subreddits now come
  from one combined request (r/ClaudeAI+OpenAI+ChatGPT+grok+LocalLLaMA+singularity), each post tagged by its
  subreddit's company. Import AI's Substack feed returned 403, so it now uses jack-clark.net/feed/ (the same
  newsletter). z.ai/blog has no listing page (404), so it was removed; GLM is still covered by the Z.ai release notes,
  Hugging Face, OpenRouter, Hacker News and Google News. The instant alerts run hourly instead of every 30 minutes
  because the repo is private (2,000 free minutes a month). GitHub Actions bumped to checkout@v5 / setup-python@v6
  (Node 20 deprecation).
- **Same day, first previews:** Hacker News and Google News results were being filed under a company just because
  the source was set up for that company (e.g. an unrelated court story under GLM). Now only official sources assign
  a company; community and press items must mention one in the headline or summary. Hacker News searches titles
  only, r/ChatGPT was dropped (mostly memes), and Codex alpha/beta builds are skipped. GitHub Models was removed:
  it was retired on 2026-07-30 and its endpoint now answers "OK" to everything. At least one free AI key (Gemini
  recommended) is now needed for written summaries; without one the agent sends plain headlines.
- **Same day, first run with a Gemini key:** the key worked, but `gemini-flash-latest` answered "503 high demand"
  and `gemini-2.5-flash` has been retired ("use gemini-3.8-flash"). AI calls now retry busy errors (429/5xx) twice,
  after 5 and 20 seconds, and the Gemini list is `gemini-flash-latest`, `gemini-3.8-flash`, `gemini-flash-lite-latest`.
- **Same day, owner asked for earlier signals and free models:** TestingCatalog and OpenRouter's new/stealth models
  now trigger hourly alerts too. New sources: models.dev (the open model database behind OpenCode) for early listings
  of Anthropic, OpenAI, xAI and Z.ai models; new free models on OpenRouter (any company); and new free models in
  OpenCode (models.dev provider `opencode`, cost 0). Free models get their own "🆓 New free models you can use"
  section in the brief. Groq (the owner's backup key) gets a shorter item list to fit its free per-minute token limit.
- **Same day, owner asked to request free-model info on demand:** Telegram commands `/free` (every model that is
  free right now on OpenRouter and in OpenCode, 🆕 for the last 14 days), `/latest` (resend the last brief) and
  `/help`. The agent has no always-on server, so it reads commands on each hourly alert run and each brief (replies
  within about an hour) and answers only the owner's chat. A `free-models` workflow run sends the list instantly.
  An always-on bot (instant replies) would need a free Cloudflare Worker; not built yet.
- **Same day, "go with all your suggestions":** (1) the top 15 items' full articles are read before writing;
  (2) a fact-check pass on a different AI provider fixes or drops unsupported stories, and the brief still goes
  out if it fails; (3) each story shows how many independent publishers back it, ⚠️ single source, and when it
  first appeared, and a story is "Official" only if an official source is among its items (the first brief had
  labelled Google News reports as Official); (4) quiet-change watchers on the models, pricing, deprecation and
  plan pages plus status feeds for Anthropic, OpenAI, xAI and Z.ai (tables are read too, where prices live);
  (5) blocked publishers (blockchain.news, basenor, tikr.com, startuphub.ai); (6) headlines sent in the last 48 h
  are given to the writer so stories aren't repeated unless there's an update; (7) daily Builder picks (GitHub
  Trending, rising repos by topic, Hugging Face trending, Show HN; one AI call picks 10); (8) a Sunday weekly
  recap from the saved briefs; (9) `/repos`, `/ask` and `/news` commands; (10) an optional always-on Cloudflare
  Worker bot for instant replies. The agent pushes data to it, so the worker does no heavy work and stays
  inside the free CPU limit. Cost check before building: about 10 AI requests a day (was 4), well inside the
  free tiers, and about 1,350 of 2,000 free GitHub minutes a month.
- **Same day, first live test of the above:** `gemini-3.8-flash` hit its free daily limit after a day of heavy
  testing ("exceeded your current quota"), so a 429 that mentions quota now skips straight to the next model, and
  3.8-flash is tried after Groq. The Google News decoder reports success as `success` (not `status`), so links were
  never decoded; fixed, and the check-sources run now tests decoding and article reading logs why items are skipped.
- **Same day, final preview:** 10 of 15 full articles were read (was 3) and links now point at the real articles.
  Two more fixes: the fact-checker now also checks the headline (one said "$2 trillion IPO" with no source behind
  it), and items published on the companies' own sites (anthropic.com, claude.com, openai.com, x.ai, z.ai, ...)
  count as Official even when they arrive through Google News. Gemini's free daily limits turned out lower than
  assumed: after a day of testing, gemini-flash-latest and gemini-3.8-flash both answered "quota exceeded" and
  gemini-flash-lite-latest wrote the brief. Normal use is about 5-6 Gemini calls a day; Groq does the fact-check.
- **2026-10-19, owner asked for GitHub + Hugging Face buzz:** "look through hugging face and … send most talked
  about github repositories or news on hugging face … scan the two sites every 3 hours and give me something to
  talk about … mainly ai focus: new models, new upgrades, new censored models, new free models, limited offers on
  models … especially anthropic and claude, open ai and chatgpt, glm, kimi, minimax and the key main models … for
  github … ai and trending repos even if it's outside the ai scope". Owner's choices: keep the 6-hour brief, the
  hourly alerts and the Builder picks as they are; watch Kimi, MiniMax, xAI/Grok, DeepSeek, Qwen, Google
  Gemini/Gemma and Meta Llama besides Claude, OpenAI and GLM; "censored" means both uncensored models and
  censorship news. Built a new **Buzz** run every 3 hours (`agent/buzz.py`, `.github/workflows/buzz.yml`):
  GitHub Trending (all repos, non-AI marked), rising AI repos, the labs' own GitHub orgs and Hugging Face uploads,
  HF trending models and Spaces, uncensored/abliterated models, HF Daily Papers, the HF blog, and two Google News
  searches for free/limited-time offers and censorship (HF and GitHub don't publish those). One AI call picks up
  to 8 with "what / why people are talking / a line to post"; links come from the items, and without AI the
  hottest item of each kind is sent. A pick isn't repeated for 7 days. Telegram (email if Telegram isn't set
  up), `/buzz` command. About 480 more GitHub minutes a month (recent runs take 1-2 minutes), about 8 more AI
  requests a day.
- **2026-11-16, owner asked for a dedicated AI Model Release Intelligence system:** "monitor official model
  announcements and release information from Anthropic (Claude), OpenAI (GPT), Moonshot AI (Kimi), Alibaba (Qwen),
  xAI (Grok) and Google (Gemini) … official sources must establish release confirmation … compare the new model
  against its predecessor … confirm successful Telegram delivery". An audit first found: the hourly alerts actually
  run every 4-9 hours (GitHub skips scheduled runs); Anthropic's model launch posts live at the site root
  (/claude-opus-5-5), which the `^/news/` pattern never matched; the "OpenAI models page" watcher only ever saw 5
  lines (the page is rendered by JavaScript); "Claude Blog" (moved to /resources/articles) and help.openai.com (403
  to bots) have been failing for weeks; and every lab except Alibaba now publishes Markdown copies of its docs.
  Built `agent/releases/` + `releases.yaml` (30 official sources + 3 early-signal listings, every endpoint checked) as a
  separate module so the digest's four-company scope is unchanged: model catalogs and changelogs are diffed as
  structured data, changes are classified deterministically into six categories with five verification statuses,
  confirmed launches are compared with their predecessor (the company's own wording first, then the catalog lineage),
  the AI only adds facts it can quote verbatim from the official text, and alerts go through a Telegram outbox that
  only counts Telegram's own confirmation (message ids saved, retries never repeat a confirmed part, receipt after
  delivery). Runs inside every "Instant alerts" run (no schedule changed) and on demand; an optional Cloudflare cron
  can start it on time. Tested against today's real launches (Gemini 4 Hydrogen, Claude Fable/Mythos 5.2, GPT-6
  Cyber, Kimi K3.1 Pro): all four detected, confirmed and compared with the right predecessor.
- **2026-11-16, owner asked for Telegram intelligence and message organisation:** "every notification must have a
  clear purpose, category, priority and consistent presentation … like a professional AI research assistant".
  Added `agent/messages.py`: ten fixed sections (only non-empty ones shown), four priorities (P0 critical to P3
  discovery) and one card layout (headline · priority/verification/date · company or speaker · what happened · why
  it matters · details · sources), with a collapsed "Explained" block (six questions) for important stories and
  A/B/C content opportunities (title, angle, why, research needed). Long reports split in order without cutting a
  card or an HTML tag. Every producer now uses it: the brief (the AI also returns section, why, details, speaker,
  explanations and content ideas; sections and priorities are enforced by rules; numbers that appear in no source are
  dropped), instant alerts (rule-based sections and priorities, minor documentation edits dropped, several sources
  merged into one card, P3 deferred to the digest), Buzz, Builder picks, the weekly report (now saved) and the release
  monitor (flagship releases P0). Priority sets timing and sound: P2/P3 arrive silently. A cross-run ledger
  (`state/sent.json`) stops a story from being delivered again by another run or with a reworded headline. New
  commands: /models, /compare (official specs side by side), /sources, /health (live workflow runs via the GitHub
  API, including how often the hourly alerts really ran), /weekly; all existing commands kept, in both bots.
- **2026-11-16, owner asked for a Weekly Model Intelligence Report:** "every Sunday, a complete overview of the AI
  models officially announced or released during the previous seven days … use the persistent model-release
  history … do not break the existing weekly recap", then "push all to main". Added `agent/releases/weekly.py`
  (`python -m agent weekly-models`), a second step of the Sunday workflow that can't fail the recap: new releases,
  comparisons with predecessors, updates (kept apart), an evidence-based competitive landscape, benchmark
  intelligence and ranked content ideas (3 videos, 3 X posts, 1 comparison, 1 educational topic). Each release is
  counted once (the report records what it covered; late detections are included and marked). Independent
  benchmarks: LMArena's official Hugging Face dataset (checked live: current, with confidence intervals), compared
  only within one leaderboard category; Aider's leaderboard was rejected as stale (last entry Oct 2025), and
  lmarena.ai, epoch.ai, swebench.com, livebench.ai and artificialanalysis.ai offer no keyless machine-readable feed.
  Release records now also keep the full comparison, explanation and API availability for the report. Archive in
  releases/weekly/, command /weeklymodels in both bots.
- **2026-11-16, owner asked for AI Skills & Open-Source Discovery Intelligence** ("a dedicated discovery engine that
  finds practical resources I can actually use … every three hours … as a curated batch … without repeating
  previously delivered resources") and to move the weekly model report to Saturday 3 pm. Added `agent/discovery/` +
  `discovery.yaml` + `discovery.yml` (every 3 h): rotated GitHub searches (32 queries), official AI organisations,
  GitHub Trending, Hugging Face Spaces/models/datasets, the official MCP registry, GitLab and Codeberg (all checked
  live; Codeberg's search was throttling, Papers with Code is closed). Deterministic evaluation: AI relevance by
  the resource's own words (a TV-debloating repo tagged claude-code was rejected), eight categories, security
  screening (secrets requests, hidden code, exposed keys, prompt injection → rejected; curl|sh → warning), six
  licence/cost classes, requirements, a 0-100 quality score where documentation, maintenance and accessibility
  outweigh stars, P0-P3 (P0 gated). A pool collects passing resources between runs; batches go through the
  release monitor's Telegram outbox and resources are marked delivered only on Telegram's confirmation; GitHub
  identity is the repository id, so renames and URL variants are not re-sent; meaningful new releases (major
  version or real new features) are sent as "what changed". Weekly model report moved to its own Saturday workflow
  (13:53 UTC = 14:53 Lagos, aiming at 3 pm with GitHub's start delay); the Sunday recap is unchanged.
