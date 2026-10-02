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
