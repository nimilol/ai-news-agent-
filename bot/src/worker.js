// Always-on Telegram bot for the AI news agent (Cloudflare Worker, free plan).
//
// Telegram sends every message here instantly (a "webhook"). The heavy work still happens on GitHub:
// after each run the agent pushes the latest brief, Builder picks, Buzz, free-model list and this week's
// briefs into KV storage, so the bot only reads and replies. Only your own chat gets answers.
//
// Secrets (set by the deploy workflow): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY,
// GROQ_API_KEY (optional), BOT_GITHUB_TOKEN (optional, enables /news). Variable: GH_REPO.

const HELP = [
  "<b>🛰 Your AI research assistant</b> (instant replies)",
  "",
  "<b>News</b>",
  "/latest · the most recent news brief",
  "/buzz · what's hot on GitHub, Hugging Face, Reddit and YouTube",
  "/repos · today's Builder picks",
  "/discoveries · the latest AI skills & open-source discoveries",
  "/weekly · the latest weekly news recap",
  "/ask <i>your question</i> · answered from this week's briefs",
  "/news · run a fresh news check now",
  "",
  "<b>Models</b>",
  "/models · recent confirmed model releases",
  "/releases · the full release history",
  "/weeklymodels · the latest Weekly Model Intelligence Report",
  "/compare <i>model A</i> vs <i>model B</i> · official specs side by side",
  "/free · models you can use for free right now",
  "",
  "<b>System</b>",
  "/sources · the official sources being monitored",
  "/health · runs, failing sources and alert delivery",
  "/help · this message",
  "",
  "Priorities: 🔴 P0 critical · 🟠 P1 high · 🔵 P2 normal · ⚪ P3 discovery (P2 and P3 arrive silently).",
].join("\n");

const ASK_PROMPT =
  "You answer questions from an AI news creator using only the news briefs below (newest last). " +
  "Answer in at most 8 short lines of plain text. Name the date and source of each fact. " +
  "If the briefs don't cover the question, say so plainly instead of guessing.";

const PUSH_KEYS = ["last_brief", "last_repos", "last_buzz", "free_models", "briefs", "last_releases", "last_models",
  "models_index", "sources", "health", "last_weekly", "last_model_weekly", "last_discovery"];

// /compare: the same simple name matching as agent/releases/catalog.py ("opus 5.5" finds claude-opus-5-5).
export function normalize(text) {
  return String(text || "").trim().toLowerCase().replace(/^[a-z0-9_.-]+\//, "").replace(/[\s_.]+/g, "-")
    .replace(/[^a-z0-9-]/g, "").replace(/-+/g, "-").replace(/^-|-$/g, "");
}

export function findModel(index, query) {
  const q = normalize(query);
  if (!q) return null;
  const tests = [(k) => k === q, (k) => k.endsWith("-" + q), (k) => k.startsWith(q + "-")];
  for (const test of tests) {
    const hits = index.filter((m) => m.keys.some(test));
    if (hits.length) {
      return hits.sort((a, b) => (a.plain === b.plain ? a.id.length - b.id.length : a.plain ? -1 : 1))[0];
    }
  }
  return null;
}

export function splitPair(text) {
  const parts = String(text || "").split(/\s+(?:vs\.?|versus|and|against)\s+|\s*,\s*|\s+\|\s+/i)
    .map((p) => p.trim()).filter(Boolean);
  return parts.length >= 2 ? [parts[0], parts[1]] : null;
}

const COMPARE_ROWS = [["context", "📏 Context window"], ["output", "📤 Output limit"], ["pricing", "💰 API pricing"],
  ["speed", "⚡ Speed"], ["multimodal", "🖼 Inputs and outputs"], ["reasoning", "🧠 Reasoning"],
  ["availability", "🌍 Availability"]];

export function renderCompare(a, b) {
  const cell = (v) => (v ? escapeHtml(v) : "<i>not established</i>");
  const lines = [`<b>⚖️ ${escapeHtml(a.name)} vs ${escapeHtml(b.name)}</b>`,
    `<i><code>${escapeHtml(a.id)}</code> vs <code>${escapeHtml(b.id)}</code></i>`];
  for (const [key, label] of COMPARE_ROWS) {
    if (a.cells[key] || b.cells[key]) {
      lines.push(`<b>${label}</b>\n• ${escapeHtml(a.name)}: ${cell(a.cells[key])}\n• ${escapeHtml(b.name)}: ${cell(b.cells[key])}`);
    }
  }
  lines.push("<i>Official catalog specs only. Benchmarks and quality are not compared here.</i>");
  return lines.join("\n\n");
}

async function compare(env, args) {
  const pair = splitPair(args);
  if (!pair) return send(env, "Name two models, e.g. <i>/compare Claude Opus 5.5 vs GPT-6.1 Terra</i>");
  const index = (await env.AGENT_KV.get("models_index", "json")) || [];
  if (!index.length) return send(env, "The model catalog isn't loaded yet. It refreshes with every release check.");
  const found = pair.map((q) => findModel(index, q));
  const missing = pair.filter((q, i) => !found[i]);
  if (missing.length) {
    return send(env, `I couldn't find ${missing.map((q) => `<b>${escapeHtml(q)}</b>`).join(" or ")} in the official ` +
      "model catalogs of the six labs I monitor. Try the model id, e.g. gpt-6.1-terra.");
  }
  return send(env, renderCompare(found[0], found[1]));
}

export async function sha256(text) {
  const data = new TextEncoder().encode(text);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function send(env, text, parseMode = "HTML") {
  const body = { chat_id: env.TELEGRAM_CHAT_ID, text, disable_web_page_preview: true };
  if (parseMode) body.parse_mode = parseMode;
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok && parseMode) {
    // Formatting problem: send it without HTML rather than nothing.
    return send(env, text.replace(/<[^>]+>/g, ""), null);
  }
  return r.ok;
}

async function sendStored(env, key, missing) {
  const messages = await env.AGENT_KV.get(key, "json");
  if (!messages || !messages.length) return send(env, missing);
  for (const m of messages) await send(env, m);
}

async function chat(baseUrl, key, model, system, user) {
  const r = await fetch(`${baseUrl}/chat/completions`, {
    method: "POST",
    headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
    body: JSON.stringify({
      model,
      temperature: 0.2,
      max_tokens: 1200,
      messages: [
        { role: "system", content: system },
        { role: "user", content: user },
      ],
    }),
  });
  if (!r.ok) throw new Error(`${model}: HTTP ${r.status}`);
  const data = await r.json();
  const text = data?.choices?.[0]?.message?.content;
  if (!text) throw new Error(`${model}: empty answer`);
  return text;
}

export async function answer(env, question) {
  const briefs = (await env.AGENT_KV.get("briefs")) || "";
  if (!briefs) return "I don't have any saved briefs yet, so I can't answer from the news.";
  const tries = [];
  if (env.GEMINI_API_KEY) {
    for (const m of ["gemini-flash-latest", "gemini-flash-lite-latest"]) {
      tries.push(["https://generativelanguage.googleapis.com/v1beta/openai", env.GEMINI_API_KEY, m, 60000]);
    }
  }
  if (env.GROQ_API_KEY) {
    for (const m of ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]) {
      tries.push(["https://api.groq.com/openai/v1", env.GROQ_API_KEY, m, 12000]);
    }
  }
  const errors = [];
  for (const [url, key, model, chars] of tries) {
    try {
      const user = `Briefs:\n${briefs.slice(-chars)}\n\nQuestion: ${question}`;
      return await chat(url, key, model, ASK_PROMPT, user);
    } catch (e) {
      errors.push(String(e.message || e));
    }
  }
  throw new Error(errors.join("; ") || "no AI key set");
}

async function runNews(env) {
  if (!env.BOT_GITHUB_TOKEN || !env.GH_REPO) {
    return send(env, "To use /news, add a <b>BOT_GITHUB_TOKEN</b> secret (see the README) and deploy the bot again.");
  }
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/actions/workflows/digest.yml/dispatches`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.BOT_GITHUB_TOKEN}`,
      accept: "application/vnd.github+json",
      "user-agent": "ai-news-bot",
      "content-type": "application/json",
    },
    body: JSON.stringify({ ref: "main", inputs: { mode: "digest" } }),
  });
  return send(env, r.status === 204
    ? "🔄 Started a fresh news check. The brief arrives in about 3 minutes."
    : `Couldn't start the news check (GitHub said HTTP ${r.status}). Check the BOT_GITHUB_TOKEN permissions.`);
}

export async function handleMessage(env, message) {
  if (String(message?.chat?.id) !== String(env.TELEGRAM_CHAT_ID)) return "ignored"; // only you
  const raw = (message.text || "").trim();
  const [first = ""] = raw.split(/\s+/);
  const command = first.replace(/^\//, "").split("@")[0].toLowerCase();
  switch (command) {
    case "free":
      await sendStored(env, "free_models", "The free-model list isn't ready yet. It refreshes every hour.");
      return command;
    case "latest":
      await sendStored(env, "last_brief", "No brief yet. The next one arrives at the next 6-hour run.");
      return command;
    case "repos":
      await sendStored(env, "last_repos", "No Builder picks yet. They arrive every morning.");
      return command;
    case "buzz":
      await sendStored(env, "last_buzz", "No Buzz yet. It arrives every 3 hours.");
      return command;
    case "releases":
      await sendStored(env, "last_releases", "No model releases recorded yet. The release monitor checks the official sources every run.");
      return command;
    case "models":
      await sendStored(env, "last_models", "No confirmed model releases recorded yet.");
      return command;
    case "weekly":
      await sendStored(env, "last_weekly", "No weekly report yet. It arrives on Sunday evening.");
      return command;
    case "weeklymodels":
      await sendStored(env, "last_model_weekly", "No Weekly Model Intelligence Report yet. It arrives on Saturday afternoon.");
      return command;
    case "discoveries":
      await sendStored(env, "last_discovery", "No discoveries sent yet. The discovery engine runs every 3 hours.");
      return command;
    case "sources":
      await sendStored(env, "sources", "The source list isn't loaded yet. It refreshes with every release check.");
      return command;
    case "health":
      await sendStored(env, "health", "No health report yet. It refreshes with every release check.");
      return command;
    case "compare":
      await compare(env, raw.slice(first.length).trim());
      return command;
    case "ask": {
      const question = raw.slice(first.length).trim();
      if (!question) {
        await send(env, "Type your question after /ask, e.g. <i>/ask what did OpenAI release this week?</i>");
        return command;
      }
      try {
        const text = await answer(env, question);
        await send(env, `<b>❓ ${escapeHtml(question)}</b>\n${escapeHtml(text.trim())}`);
      } catch (e) {
        await send(env, `Sorry, I couldn't answer that right now (${escapeHtml(String(e.message || e).slice(0, 120))}).`);
      }
      return command;
    }
    case "news":
      await runNews(env);
      return command;
    default:
      await send(env, HELP);
      return "help";
  }
}

// Optional dependable trigger for the release monitor. GitHub often skips scheduled runs when it is busy (the
// hourly "Instant alerts" have run every 4-9 hours in practice), so when the deploy sets a cron for this worker
// (repository variable RELEASE_CHECK_CRON), Cloudflare starts the "Model releases" workflow on time instead.
export async function dispatchReleaseCheck(env) {
  if (!env.BOT_GITHUB_TOKEN || !env.GH_REPO) return "not configured";
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/actions/workflows/releases.yml/dispatches`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.BOT_GITHUB_TOKEN}`,
      accept: "application/vnd.github+json",
      "user-agent": "ai-news-bot",
      "content-type": "application/json",
    },
    body: JSON.stringify({ ref: "main", inputs: { mode: "releases" } }),
  });
  return r.status === 204 ? "started" : `GitHub said HTTP ${r.status}`;
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatchReleaseCheck(env));
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/telegram") {
      const expected = (await sha256(`hook:${env.TELEGRAM_BOT_TOKEN}`)).slice(0, 32);
      if (request.headers.get("x-telegram-bot-api-secret-token") !== expected) {
        return new Response("forbidden", { status: 403 });
      }
      const update = await request.json();
      if (update.message) ctx.waitUntil(handleMessage(env, update.message));
      return new Response("ok"); // answer Telegram at once; the reply is sent in the background
    }
    if (request.method === "POST" && url.pathname === "/push") {
      const expected = `Bearer ${await sha256(`push:${env.TELEGRAM_BOT_TOKEN}`)}`;
      if (request.headers.get("authorization") !== expected) {
        return new Response("forbidden", { status: 403 });
      }
      const { key, value } = await request.json();
      if (!PUSH_KEYS.includes(key)) return new Response("unknown key", { status: 400 });
      await env.AGENT_KV.put(key, typeof value === "string" ? value : JSON.stringify(value));
      return new Response("saved");
    }
    return new Response("AI news bot is running.");
  },
};
