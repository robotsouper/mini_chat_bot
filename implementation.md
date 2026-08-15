# Implementation Plan: Telegram Assistant Bot

This document breaks the design in [telegram_bot_design.md](telegram_bot_design.md)
into small, ordered tasks. **Each task is sized to be one commit.** Every task
states what we are doing and which files we touch, and ends with a "Done when"
check so it can be verified before moving on.

Build in order — later tasks depend on earlier ones.

---

## Conventions

- **One task = one commit.** Keep commits small and self-contained.
- **Stack:** Python 3.11+, `python-telegram-bot` (v21+, async), `anthropic` SDK.
- **Model:** default `claude-sonnet-4-6` (cheap, good for chat), configurable via `MODEL`.
- **LLM access:** the Anthropic **API** (a Pro subscription does not grant API access — see the design doc). You need an API key with a small amount of credit.
- **Secrets** live in environment variables, never in the repo.

### Target repository layout (built up over the tasks)

```
ai_chat_bot/
  bot.py                  # entrypoint: Telegram handlers, wiring
  config.py               # env/config loading
  llm.py                  # Claude client + request assembly
  memory.py               # conversation memory store
  tools/
    __init__.py
    registry.py           # active tool set + tool loop
    custom.py             # custom function tools
  knowledge/
    __init__.py
    store.py              # vector store interface
    ingest.py             # offline ingestion script
  requirements.txt
  .env.example            # documents required env vars (no real secrets)
  .gitignore
  Procfile                # Railway start command
  README.md               # setup + run instructions
```

---

## Phase 0 — Project setup

### Task 1 — Repository scaffolding

**Doing:** Create the project skeleton and dependency/config plumbing. No bot
logic yet — just enough that the repo installs and configuration loads.

**Files:**
- `requirements.txt` *(new)* — `python-telegram-bot`, `anthropic`.
- `.gitignore` *(new)* — ignore `.env`, `__pycache__/`, local data files.
- `.env.example` *(new)* — document `TELEGRAM_TOKEN`, `ANTHROPIC_API_KEY`, `MODEL`.
- `config.py` *(new)* — load env vars into a typed config object; fail fast if required ones are missing.
- `README.md` *(new)* — how to create the bot (BotFather), install deps, set env vars, run.

**Done when:** `pip install -r requirements.txt` succeeds and `python -c "import config"` loads config from a local `.env` without error.

---

## Phase 1 — M1: Skeleton bot

### Task 2 — Minimal single-turn bot

**Doing:** A working bot that receives a text message, sends it to Claude, and
replies. No memory, no allowlist, no group logic yet — prove the end-to-end path.

**Files:**
- `llm.py` *(new)* — Claude client wrapper; a `respond(text) -> str` that calls `client.messages.create` with the system prompt and returns the reply text.
- `bot.py` *(new)* — `ApplicationBuilder` with the token, a `MessageHandler` for text that calls `llm.respond`, `run_polling()`.
- `README.md` *(changed)* — add "run the bot" section.

**Done when:** Running `python bot.py` and messaging the bot (in a private chat for now) returns an LLM-generated reply.

### Task 3 — Chat allowlist gate

**Doing:** Restrict the bot to approved chats. Any message from a chat not in the
allowlist is ignored.

**Files:**
- `config.py` *(changed)* — parse `ALLOWED_CHAT_IDS` (comma-separated) into a set of ints.
- `.env.example` *(changed)* — document `ALLOWED_CHAT_IDS`.
- `bot.py` *(changed)* — at the top of the handler, drop updates whose `chat_id` is not allowed.

**Done when:** The bot replies in an allowed chat and stays silent in a non-allowed one. (Tip: add a temporary `/whereami` or log line to discover a group's `chat_id`.)

### Task 4 — Group addressing and reply formatting

**Doing:** Make it behave correctly in a group: only respond when addressed,
strip the bot mention from the text, show a typing indicator, and split long
replies to respect Telegram's 4096-char limit.

**Manual step (no code):** In BotFather, keep **group privacy mode ON** so Telegram
only delivers messages that @-mention or reply to the bot. Document this in README.

**Files:**
- `bot.py` *(changed)* — guard that the message is addressed to the bot (mention or reply); strip the `@botname` from the text before sending to the model; send `ChatAction.TYPING`; split replies on paragraph boundaries into ≤4096-char messages.
- `llm.py` *(changed)* — accept the cleaned message text.
- `README.md` *(changed)* — document the BotFather privacy-mode step.

**Done when:** In a group, the bot replies when @-mentioned or replied to, stays silent during normal chatter, and long answers arrive as multiple messages.

### Task 5 — Deploy to Railway

**Doing:** Get the bot running 24/7 off the developer's laptop, with the same
polling code.

**Host:** **Railway**, as the design doc's §7 recommends. Its free tier is gone
(~$5/month on the Hobby plan), and we accept that cost in exchange for keeping
the zero-ops model: no OS patching, no process supervision, no server to own.

**Files:**
- `Procfile` *(new)* — `worker: python bot.py`.
- `.python-version` *(new)* — pin the interpreter for Railway's builder.
- `README.md` *(changed)* — Railway deploy steps: create the project, set env vars in the dashboard, deploy, confirm exactly one worker.

**Done when:** The bot responds from Railway with the developer's laptop closed. Exactly one worker is running (two would conflict on the same token).

---

## Phase 2 — M2: Conversation memory

### Task 6 — In-memory per-group history (multi-turn)

**Doing:** Give the bot short-term memory so it stays coherent across turns.
Start with an in-process store (resets on restart — fine for this step).

**Files:**
- `memory.py` *(new)* — a store keyed by `chat_id` holding an ordered list of `{"role", "content"}` turns; `load(chat_id)`, `append(chat_id, role, content)`.
- `llm.py` *(changed)* — accept prior `messages` and pass the full history to `client.messages.create`.
- `bot.py` *(changed)* — load history, append the user turn, call the model, append the reply.

**Done when:** The bot correctly answers a follow-up that depends on the previous message (e.g. "what did I just ask?").

### Task 7 — Sender attribution

**Doing:** Since a group has many speakers, record who said each turn and surface
it to the model so it can address people and track who asked what.

**Files:**
- `memory.py` *(changed)* — store a sender label on each user turn.
- `bot.py` *(changed)* — capture the Telegram display name/username.
- `llm.py` *(changed)* — prefix user content with the sender name when assembling the request.

**Done when:** The bot can answer "who asked about X?" and addresses different users by name.

### Task 8 — `/reset` command and history trimming

**Doing:** Let the group clear context, and cap history so requests stay within a
sane token budget.

**Files:**
- `memory.py` *(changed)* — `reset(chat_id)`; trim to the last N turns / ~8k tokens on append.
- `bot.py` *(changed)* — register a `/reset` `CommandHandler`; add `/help` text.

**Done when:** `/reset` clears the group's memory; a very long conversation drops oldest turns instead of growing unbounded.

### Task 9 — Durable memory (Redis)

**Doing:** Replace the in-memory store with Redis so a restart/redeploy doesn't
wipe context. Same interface, swapped backend.

**Files:**
- `requirements.txt` *(changed)* — add `redis`.
- `.env.example` *(changed)* — document `REDIS_URL`.
- `config.py` *(changed)* — load `REDIS_URL`.
- `memory.py` *(changed)* — back the store with Redis (per-`chat_id` key, TTL-based expiry); keep the same `load/append/reset` API.
- `README.md` *(changed)* — add the Railway Redis plugin step.

**Done when:** Memory survives a bot restart; adding the Redis plugin on Railway and setting `REDIS_URL` works in production.

---

## Phase 3 — M3: Tools

### Task 10 — Tool loop with one custom function

**Doing:** Introduce the tool-calling loop and prove it with a single custom
function tool (Claude calls it, our code runs it, result is fed back).

**Files:**
- `tools/__init__.py` *(new)*.
- `tools/custom.py` *(new)* — one example tool (e.g. current time / a simple lookup) with a JSON schema and its handler.
- `tools/registry.py` *(new)* — collect active tool definitions; run the call→execute→feed-back loop (SDK tool runner or a manual loop).
- `llm.py` *(changed)* — pass `tools` and route tool turns through the loop.
- `config.py` *(changed)* — a flag/list to enable specific tools.

**Done when:** Asking something that needs the tool causes the bot to call it and answer using the result; normal questions still work without tools.

### Task 11 — Anthropic server-side tools (web search, code execution)

**Doing:** Add hosted tools that need no execution code on our side.

**Files:**
- `tools/registry.py` *(changed)* — add `web_search` and `code_execution` tool entries, gated by config.
- `config.py` *(changed)* — flags `ENABLE_WEB_SEARCH`, `ENABLE_CODE_EXEC`.
- `.env.example` *(changed)* — document the flags.

**Done when:** With web search enabled, the bot answers a current-events question with fresh info; with code execution enabled, it can compute/produce a result.

### Task 12 — MCP connector

**Doing:** Let the bot use tools from a remote MCP server.

**Files:**
- `config.py` *(changed)* — read MCP server config (`MCP_SERVERS`: name + URL).
- `.env.example` *(changed)* — document the MCP config.
- `llm.py` *(changed)* — when MCP servers are configured, call `client.beta.messages.create` with `mcp_servers` + matching `mcp_toolset` entries and the `mcp-client-2025-11-20` beta flag.
- `tools/registry.py` *(changed)* — include the MCP toolset in the active tool set.

**Done when:** With an MCP server configured, the bot can invoke one of its tools and use the result in a reply.

---

## Phase 4 — M4: Knowledge base (RAG)

### Task 13 — Vector store + ingestion script

**Doing:** Build the offline pipeline that turns our documents into a searchable
index. (Embeddings need a separate provider — Voyage AI — since Claude has no
embeddings endpoint.)

**Files:**
- `requirements.txt` *(changed)* — add the embeddings client (`voyageai`) and a vector store (`faiss-cpu` or `chromadb`).
- `.env.example` *(changed)* — document `VOYAGE_API_KEY`.
- `config.py` *(changed)* — load the embeddings key and index path.
- `knowledge/__init__.py` *(new)*.
- `knowledge/store.py` *(new)* — `KnowledgeBase` with `search(query, k) -> chunks`; embed + similarity query against the index.
- `knowledge/ingest.py` *(new)* — CLI: read source docs, chunk (~500–800 tokens), embed, write the index.

**Done when:** Running `python -m knowledge.ingest <docs>` builds an index, and a quick `store.search("...")` returns relevant chunks.

### Task 14 — Wire retrieval into the bot

**Doing:** Use retrieved chunks to ground answers, only when they're relevant.

**Files:**
- `llm.py` *(changed)* — before the model call, embed the user message, fetch top-K chunks, and if they clear a relevance threshold, add them to the request with instructions to answer from and cite them; otherwise answer normally.
- `bot.py` *(changed)* — pass the `KnowledgeBase` into the request path.
- `config.py` *(changed)* — `RAG_TOP_K`, `RAG_THRESHOLD`.

**Done when:** A question covered by the docs is answered from them (with a citation); an unrelated question skips retrieval and answers normally.

---

## Phase 5 — M5: Hardening

### Task 15 — Structured logging and usage tracking

**Doing:** Make the bot observable — one structured log line per turn.

**Files:**
- `bot.py` / `llm.py` *(changed)* — log `chat_id`, latency, token usage, tools invoked, and the Anthropic request ID on errors.
- `config.py` *(changed)* — `LOG_LEVEL`.

**Done when:** Each interaction emits a structured log line; failures include the request ID; daily token usage can be read from logs.

### Task 16 — Per-group rate limiting

**Doing:** Bound cost and prevent spam by capping how often each group can trigger the bot.

**Files:**
- `bot.py` *(changed)* — a simple per-`chat_id` rate limiter; over-limit messages get a brief "slow down" reply or are dropped.
- `config.py` *(changed)* — `RATE_LIMIT_PER_MINUTE`.

**Done when:** Rapid-fire messages in one group are throttled; other groups are unaffected.

### Task 17 — Retention policy and graceful errors

**Doing:** Expire old memory per policy and make failures user-friendly.

**Files:**
- `memory.py` *(changed)* — enforce a retention TTL (align with the design's policy).
- `bot.py` *(changed)* — wrap the model call so unrecoverable errors send a friendly fallback message instead of failing silently; rely on the SDK's built-in retry for 429/5xx.
- `README.md` *(changed)* — document the retention policy and env knobs.

**Done when:** Memory older than the TTL is gone; a forced API error produces a graceful message to the group, not a crash.

---

## Suggested commit messages

```
1.  chore: scaffold project (deps, config, env template)
2.  feat: minimal single-turn Claude reply over Telegram
3.  feat: restrict bot to allowlisted chats
4.  feat: group addressing, typing indicator, message splitting
5.  chore: deploy to Railway (Procfile + docs)
6.  feat: in-memory per-group conversation history
7.  feat: attribute stored turns to their sender
8.  feat: /reset command and history trimming
9.  feat: back memory with Redis for durability
10. feat: tool-calling loop with a custom function tool
11. feat: enable web search and code execution server tools
12. feat: MCP connector support
13. feat: RAG vector store and ingestion script
14. feat: ground replies with retrieved knowledge
15. feat: structured logging and token-usage tracking
16. feat: per-group rate limiting
17. feat: memory retention policy and graceful error handling
```
