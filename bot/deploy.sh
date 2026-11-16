#!/usr/bin/env bash
# Deploys the Telegram bot to Cloudflare Workers and points Telegram at it.
# Run by .github/workflows/deploy-bot.yml; needs the secrets listed in the README.
set -euo pipefail
cd "$(dirname "$0")"

for v in CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
  if [ -z "${!v:-}" ]; then echo "::error::Missing the $v secret. See 'Instant replies (Cloudflare)' in the README."; exit 1; fi
done

npm install --no-save --silent wrangler@4
W="npx wrangler"

write_config() {  # $1 = KV namespace id (may be empty on the very first pass)
  {
    echo 'name = "ai-news-bot"'
    echo 'main = "src/worker.js"'
    echo 'compatibility_date = "2025-09-01"'
    echo 'workers_dev = true'
    echo ''
    echo '[vars]'
    echo "GH_REPO = \"${GITHUB_REPOSITORY:-}\""
    if [ -n "${RELEASE_CHECK_CRON:-}" ]; then
      # Opt-in: Cloudflare starts the "Model releases" workflow on this schedule (needs BOT_GITHUB_TOKEN).
      echo ''
      echo '[triggers]'
      echo "crons = [\"$RELEASE_CHECK_CRON\"]"
    fi
    if [ -n "$1" ]; then
      echo ''
      echo '[[kv_namespaces]]'
      echo 'binding = "AGENT_KV"'
      echo "id = \"$1\""
    fi
  } > wrangler.toml
}

find_kv() {
  $W kv namespace list 2>/dev/null | node -e '
    let s = ""; process.stdin.on("data", d => s += d).on("end", () => {
      try { const n = JSON.parse(s).find(x => x.title === "ai-news-bot-AGENT_KV" || x.title === "AGENT_KV");
            console.log(n ? n.id : ""); } catch { console.log(""); } });'
}

write_config ""
KV_ID="$(find_kv)"
if [ -z "$KV_ID" ]; then
  echo "Creating the bot's storage (KV namespace)…"
  $W kv namespace create AGENT_KV
  KV_ID="$(find_kv)"
fi
if [ -z "$KV_ID" ]; then echo "::error::Could not create or find the KV namespace. Check the API token permissions."; exit 1; fi
write_config "$KV_ID"

# A workers.dev address needs an account-wide subdomain. New accounts don't have one yet: register one.
CF_API="https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/subdomain"
SUB="$(curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" "$CF_API" | node -e '
  let s = ""; process.stdin.on("data", d => s += d).on("end", () => {
    try { console.log(JSON.parse(s).result?.subdomain || ""); } catch { console.log(""); } });')"
if [ -z "$SUB" ]; then
  WANT="ainews-$(printf '%s' "$CLOUDFLARE_ACCOUNT_ID" | sha256sum | cut -c1-8)"
  echo "Registering the workers.dev subdomain $WANT…"
  RESULT="$(curl -s -X PUT -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H 'content-type: application/json' \
    -d "{\"subdomain\":\"$WANT\"}" "$CF_API")"
  echo "$RESULT" | grep -Eq '"success": *true' || {
    echo "::error::Could not register a workers.dev subdomain ($RESULT). Open Cloudflare → Workers & Pages once, pick a subdomain, then run this again."; exit 1; }
  sleep 5
fi

echo "Deploying the worker…"
$W deploy 2>&1 | tee deploy.log
URL="$(grep -oE 'https://[a-zA-Z0-9.-]+\.workers\.dev' deploy.log | head -1 || true)"
if [ -z "$URL" ]; then
  echo "::error::Deployed, but no workers.dev address was printed. Open Cloudflare → Workers & Pages once to set up your workers.dev subdomain, then run this again."
  exit 1
fi

echo "Saving the bot's secrets…"
put_secret() { if [ -n "${2:-}" ]; then printf '%s' "$2" | $W secret put "$1" >/dev/null && echo "  set $1"; fi; }
put_secret TELEGRAM_BOT_TOKEN "$TELEGRAM_BOT_TOKEN"
put_secret TELEGRAM_CHAT_ID "$TELEGRAM_CHAT_ID"
put_secret GEMINI_API_KEY "${GEMINI_API_KEY:-}"
put_secret GROQ_API_KEY "${GROQ_API_KEY:-}"
put_secret BOT_GITHUB_TOKEN "${BOT_GITHUB_TOKEN:-}"

echo "Connecting Telegram to the bot…"
HOOK="$(printf '%s' "hook:$TELEGRAM_BOT_TOKEN" | sha256sum | cut -c1-32)"
RESULT="$(curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  --data-urlencode "url=$URL/telegram" --data-urlencode "secret_token=$HOOK" \
  --data-urlencode 'allowed_updates=["message"]')"
echo "$RESULT" | grep -Eq '"ok": *true' || { echo "::error::Telegram refused the webhook: $RESULT"; exit 1; }

curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setMyCommands" -H 'content-type: application/json' -d '{"commands":[
  {"command":"free","description":"Free models you can use right now"},
  {"command":"latest","description":"The latest news brief"},
  {"command":"repos","description":"Today'"'"'s Builder picks"},
  {"command":"discoveries","description":"Latest AI skills & open-source discoveries"},
  {"command":"buzz","description":"What'"'"'s hot on GitHub and Hugging Face"},
  {"command":"ask","description":"Ask about this week'"'"'s news"},
  {"command":"models","description":"Recent confirmed model releases"},
  {"command":"releases","description":"Model release history"},
  {"command":"compare","description":"Compare two models: /compare A vs B"},
  {"command":"modelaccess","description":"Free or cheap access to any model"},
  {"command":"weekly","description":"The latest weekly news recap"},
  {"command":"weeklymodels","description":"The latest Weekly Model Intelligence Report"},
  {"command":"sources","description":"Official sources being monitored"},
  {"command":"health","description":"Agent health"},
  {"command":"news","description":"Run a fresh news check now"},
  {"command":"help","description":"What I can do"}]}' >/dev/null

echo "$URL" > url.txt
echo "Bot is live at $URL"
