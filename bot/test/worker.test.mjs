// Offline tests for the bot: node --test bot/test
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { dispatchReleaseCheck, handleMessage, sha256 } from "../src/worker.js";

function setup(kv = {}) {
  const store = { ...kv };
  const sent = [];
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    if (String(url).includes("sendMessage")) {
      sent.push(JSON.parse(init.body).text);
      return new Response("{}", { status: 200 });
    }
    if (String(url).includes("chat/completions")) {
      return Response.json({ choices: [{ message: { content: "OpenAI released GPT-6 on 2 Oct (OpenAI News)." } }] });
    }
    if (String(url).includes("dispatches")) return new Response(null, { status: 204 });
    return new Response("not found", { status: 404 });
  };
  const env = {
    TELEGRAM_BOT_TOKEN: "123:abc",
    TELEGRAM_CHAT_ID: "42",
    GEMINI_API_KEY: "g",
    GH_REPO: "nimilol/ai-news-agent-",
    AGENT_KV: {
      get: async (k, type) => (k in store ? (type === "json" ? JSON.parse(store[k]) : store[k]) : null),
      put: async (k, v) => { store[k] = v; },
    },
  };
  return { env, sent, calls, store };
}

const msg = (text, id = 42) => ({ chat: { id }, text });

test("commands reply from stored data", async () => {
  const { env, sent } = setup({ free_models: JSON.stringify(["free list"]), last_brief: JSON.stringify(["b1", "b2"]),
                                last_buzz: JSON.stringify(["🔥 buzz"]) });
  await handleMessage(env, msg("/free"));
  await handleMessage(env, msg("/latest@my_bot"));
  await handleMessage(env, msg("/repos"));
  await handleMessage(env, msg("/buzz"));
  assert.deepEqual(sent, ["free list", "b1", "b2", "No Builder picks yet. They arrive every morning.", "🔥 buzz"]);
});

test("strangers are ignored, unknown text gets help", async () => {
  const { env, sent } = setup();
  assert.equal(await handleMessage(env, msg("/free", 99)), "ignored");
  await handleMessage(env, msg("hello"));
  assert.equal(sent.length, 1);
  assert.match(sent[0], /Your AI news agent/);
});

test("/ask answers from the briefs", async () => {
  const { env, sent, calls } = setup({ briefs: "# Brief\nOpenAI GPT-6 ..." });
  await handleMessage(env, msg("/ask what did OpenAI release?"));
  assert.match(sent[0], /❓ what did OpenAI release\?/);
  assert.match(sent[0], /GPT-6/);
  assert.ok(calls.some((c) => c.url.includes("generativelanguage.googleapis.com")));
});

test("/news starts the GitHub workflow only with a token", async () => {
  let { env, sent } = setup();
  await handleMessage(env, msg("/news"));
  assert.match(sent[0], /BOT_GITHUB_TOKEN/);
  ({ env, sent } = setup());
  env.BOT_GITHUB_TOKEN = "t";
  await handleMessage(env, msg("/news"));
  assert.match(sent[0], /Started a fresh news check/);
});

test("webhook and push endpoints check their secrets", async () => {
  const { env, store } = setup();
  const ctx = { waitUntil: () => {} };
  const bad = await worker.fetch(new Request("https://x/telegram", { method: "POST", body: "{}" }), env, ctx);
  assert.equal(bad.status, 403);
  const hook = (await sha256("hook:123:abc")).slice(0, 32);
  const ok = await worker.fetch(new Request("https://x/telegram", {
    method: "POST", body: JSON.stringify({ message: msg("/help") }),
    headers: { "x-telegram-bot-api-secret-token": hook },
  }), env, ctx);
  assert.equal(ok.status, 200);
  const push = await worker.fetch(new Request("https://x/push", {
    method: "POST", body: JSON.stringify({ key: "last_repos", value: ["r1"] }),
    headers: { authorization: `Bearer ${await sha256("push:123:abc")}` },
  }), env, ctx);
  assert.equal(push.status, 200);
  assert.equal(store.last_repos, JSON.stringify(["r1"]));
  const denied = await worker.fetch(new Request("https://x/push", {
    method: "POST", body: JSON.stringify({ key: "last_repos", value: [] }), headers: { authorization: "Bearer nope" },
  }), env, ctx);
  assert.equal(denied.status, 403);
});

test("/releases replies with the latest model releases pushed by the agent", async () => {
  const { env, sent } = setup({ last_releases: JSON.stringify(["🧠 Latest model releases"]) });
  await handleMessage(env, msg("/releases"));
  assert.deepEqual(sent, ["🧠 Latest model releases"]);
  const empty = setup();
  await handleMessage(empty.env, msg("/releases"));
  assert.match(empty.sent[0], /No model releases recorded yet/);
});

test("the optional cron starts the Model releases workflow, only with a token", async () => {
  const { env, calls } = setup();
  assert.equal(await dispatchReleaseCheck(env), "not configured");
  assert.equal(calls.length, 0);
  env.BOT_GITHUB_TOKEN = "t";
  assert.equal(await dispatchReleaseCheck(env), "started");
  assert.match(calls[0].url, /actions\/workflows\/releases\.yml\/dispatches$/);
  assert.equal(JSON.parse(calls[0].init.body).inputs.mode, "releases");
  let waited;
  await worker.scheduled({}, env, { waitUntil: (p) => { waited = p; } });
  assert.equal(await waited, "started");
});

test("the agent can push the release list", async () => {
  const { env, store } = setup();
  const r = await worker.fetch(new Request("https://x/push", {
    method: "POST", body: JSON.stringify({ key: "last_releases", value: ["x"] }),
    headers: { authorization: `Bearer ${await sha256("push:123:abc")}` },
  }), env, { waitUntil: () => {} });
  assert.equal(r.status, 200);
  assert.equal(store.last_releases, JSON.stringify(["x"]));
});
