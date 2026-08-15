# Design Doc: General-Purpose Telegram Assistant Bot

| | |
|---|---|
| **Status** | Draft |
| **Author** | f.qingyuan123@gmail.com |
| **Last updated** | 2026-08-09 |
| **Approach** | Build-it-yourself: Python backend calling the Claude API directly |

---

## 1. Summary

We are building a general-purpose conversational assistant delivered through a
Telegram bot. The bot lives in a small, known set of **group chats**. When
addressed, it reads the group message, calls the Claude API for a response, and
posts the reply back to the group. A Python backend owns all logic in the
middle — no third-party agent platform sits between the bot and the model.

Beyond basic Q&A, the assistant supports three capabilities:

1. **Conversation memory** — it remembers earlier messages in the group thread, so replies stay coherent across turns and across different speakers.
2. **Knowledge base (RAG)** — it can answer from our own documents, not just the model's general knowledge.
3. **Tools** — it can call MCP servers, custom functions, and Anthropic's built-in server tools (web search, code execution).

The service runs 24/7 on a managed hosting platform (Railway),
so no developer machine needs to stay on.

---

## 2. Goals and non-goals

### Goals

- Deliver a responsive, general-purpose assistant inside a small set of Telegram group chats.
- Keep the group conversation coherent across multiple turns and multiple speakers.
- Respond only when addressed, and only in approved chats.
- Ground answers in our own knowledge base when relevant.
- Support extensible tool use (MCP + custom functions) without re-architecting.
- Run continuously without a developer's computer being on.
- Keep the design vendor-neutral enough to swap the underlying model if needed.

### Non-goals

- No visual flow-builder or no-code platform (that is the alternative approach; see the original comparison doc).
- No multi-channel support in v1 (Telegram only; not Slack/web/SMS).
- No fine-tuning or self-hosted models — we call the hosted Claude API.
- No image *generation* in v1 (image *understanding* is possible later via vision).
- No payment/billing features.

---

## 3. Background

Telegram bots are created through **@BotFather** and driven by Telegram's free,
open **Bot API**. BotFather issues a bot token; that token is the only thing
Telegram requires to send and receive messages. The bot's "intelligence" is
entirely up to the backend we write.

We call the **Claude API** (Anthropic) for the language model. The default model
is `claude-opus-4-8` (most capable). For a high-volume chat workload where cost
matters more than peak capability, `claude-sonnet-4-6` is a cheaper drop-in — the
code is identical apart from the model string.

The API is **stateless**: every request must carry the full conversation context
we want the model to see. Memory, retrieval, and tool orchestration are therefore
our responsibility, which is exactly why this approach gives full control.

---

## 4. High-level architecture

```
                         ┌──────────────────────────────────────────────┐
                         │              Our backend (Python)             │
                         │                                               │
 Telegram group ──────▶  │  1. Telegram handler (python-telegram-bot)    │
      ▲                  │  2. Gate: approved chat? addressed to bot?    │
      │                  │  3. Load the group's conversation memory      │
      │                  │  4. Retrieve KB chunks (RAG, if relevant)     │
      │                  │  5. Assemble request + tools                  │
      │                  │  6. Call Claude API  ─────────────────────────┼──▶  Claude API
      │                  │  7. Run any tool calls (loop)                 │◀──  (Anthropic)
      │                  │  8. Persist memory, send reply                │
      └──────────────────┤                                               │
        reply to group     └──────────────┬───────────────┬──────────────┘
                                          │               │
                                   ┌──────▼─────┐  ┌───────▼────────┐
                                   │  Storage   │  │ Vector store   │
                                   │ (history)  │  │  (KB / RAG)    │
                                   └────────────┘  └────────────────┘
                                          │
                                   ┌──────▼──────────────────────┐
                                   │ Tools: MCP servers,          │
                                   │ custom fns, web search, etc. │
                                   └──────────────────────────────┘
```

Everything inside the dashed box is our code, deployed as a single always-on
service. The external dependencies are the Telegram Bot API, the Claude API, an
embeddings provider (for RAG), and any MCP servers we connect.

---

## 5. Components and detailed design

### 5.1 Telegram integration

We use **`python-telegram-bot`** (v21+, async). The backend runs in **polling**
mode: the process long-polls Telegram for new updates. Polling requires no public
URL or inbound firewall rules, which keeps deployment simple. (A webhook variant
is described in §7 as future work.)

**Group-chat behavior — when the bot responds.** In a group, we do not want the
bot replying to every message. Two mechanisms govern this:

- **Telegram bot privacy mode** (set in BotFather). We keep privacy mode **on**,
  so Telegram only delivers messages that are directed at the bot: those that
  **@-mention** it, and those that **reply** to one of its messages. This keeps the
  bot quiet during normal group chatter and keeps our token spend down.
- **Chat allowlist.** The backend checks each update's `chat_id` against a
  configured list of approved group chats (§7). Messages from any other chat are
  ignored. This prevents the bot from being added to and used in unapproved groups.

If we later want the bot to follow the whole conversation passively (not just when
addressed), we would disable privacy mode and add our own addressing heuristics —
noted as future work (§12).

Handlers:

- `MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)` — group messages that reach the bot (mentions/replies under privacy mode).
- `CommandHandler("help", ...)`, `("reset", ...)` — control commands (see below).

**Sender attribution.** Because a group has many speakers, each stored user turn
records *who* said it (Telegram display name / username), and we surface that to
the model (e.g. prefixing content with the sender name) so it can address people
correctly and track who asked what.

**Commands.** `/reset` clears the group's conversation memory (any group member
may trigger it). `/help` sends static usage text. `/start` is omitted since the
bot is added to groups rather than started in a private chat.

**Message length:** Telegram caps a single message at 4096 characters. Long model
replies are split into multiple messages on paragraph boundaries.

**Typing indicator:** while the model is working, we send Telegram's "typing…"
chat action so the group sees the bot is responsive.

### 5.2 LLM integration

We call `client.messages.create()` (Anthropic Python SDK). Core parameters:

- `model`: `claude-opus-4-8` (config-driven, so it can be swapped).
- `max_tokens`: `1024` for chat replies; raised for tool-heavy turns.
- `system`: a system prompt describing the assistant's persona, tone, and rules.
- `messages`: the assembled conversation (memory + current turn; see §5.3).
- `tools`: the active tool set (see §5.5), when tools are enabled for the turn.

Requests that may produce long output use **streaming** (`client.messages.stream`)
to avoid HTTP timeouts and to allow incremental "typing" updates.

The system prompt is kept **stable** (no per-request timestamps or user IDs baked
in) so that Anthropic **prompt caching** can cache the system prefix across
requests, reducing cost and latency. Volatile context (retrieved KB chunks, the
user's latest message) is placed after the cached prefix.

### 5.3 Conversation memory

Because the API is stateless, we store and replay conversation history ourselves.
Memory is **shared per group** — one thread per chat, not per person — so the bot
follows a group conversation the way a participant would.

- **Scope:** keyed by Telegram `chat_id` (one thread per group chat).
- **Shape:** an ordered list of `{"role": "user" | "assistant", "content": ...}`
  turns. Each user turn carries the sender's identity (see §5.1) so the model
  knows who is speaking.
- **What we store:** the messages that reach the bot (mentions/replies) and the
  bot's replies. Under privacy mode we do not see every group message, so memory
  is the thread of interactions *with* the bot, not a full transcript of the group.
- **Storage:** see §6. In-memory dict for the prototype; a persistent store for production.
- **Trimming:** we cap history by token budget (e.g. keep the last N turns / ~8k
  tokens). When the cap is exceeded, oldest turns are dropped. For very long
  threads we can later adopt server-side **compaction** to summarize older context
  instead of dropping it.
- **Reset:** `/reset` deletes the stored history for that group.

On each turn we append the incoming (attributed) user message, call the model with
the full (trimmed) history, then append the assistant reply before persisting.

### 5.4 Knowledge base (RAG)

To answer from our own content, we use retrieval-augmented generation.

**Ingestion (offline, run when documents change):**

1. Collect source documents (Markdown, PDFs, text, or scraped URLs).
2. Split each document into overlapping chunks (~500–800 tokens).
3. Generate an embedding vector per chunk using an embeddings provider
   (**Voyage AI** is Anthropic's recommended embeddings provider; an alternative
   provider can be substituted). *Note: the Claude API does not provide an
   embeddings endpoint, so this is a separate dependency.*
4. Store `{chunk_text, embedding, source_metadata}` in a vector store.

**Retrieval (per incoming turn):**

1. Embed the user's message with the same embeddings model.
2. Query the vector store for the top-K most similar chunks.
3. If similarity clears a relevance threshold, insert those chunks into the
   request (as context in the system prompt's volatile section, or as a
   dedicated context block) with instructions to answer from them and cite them.
4. If nothing clears the threshold, skip retrieval and answer normally.

**Vector store choice:** start with a lightweight embedded store (e.g. a local
FAISS/Chroma index bundled with the service, or a hosted vector DB such as
Pinecone/pgvector). The interface is abstracted behind a `KnowledgeBase.search()`
method so the backend can change stores without touching request assembly.

### 5.5 Tools (MCP, custom functions, server tools)

The assistant can call tools. Claude decides *when* to call a tool from the tool
descriptions we provide; our backend executes the call and feeds the result back,
looping until the model produces a final answer. Three kinds of tools are in scope:

**a) Custom functions (client-side).** We define a tool with a JSON schema; when
Claude calls it, our code runs the function (e.g. a database lookup, an internal
API call) and returns the result. The SDK's **tool runner**
(`client.beta.messages.tool_runner`) can drive the call→execute→feed-back loop
automatically; alternatively we run a manual loop for fine-grained control
(logging, approval gates).

**b) MCP tools.** We support the Model Context Protocol so external capabilities
can be added without custom glue:

- **MCP connector** (preferred): Claude connects to a remote MCP server
  server-side. We pass `mcp_servers=[{"type": "url", "url": ..., "name": ...}]`
  plus a matching `tools=[{"type": "mcp_toolset", "mcp_server_name": ...}]` entry,
  with the beta flag `mcp-client-2025-11-20`, on `client.beta.messages.create`.
- **Local MCP servers**: for MCP servers we run ourselves, the SDK's MCP helpers
  (`anthropic[mcp]`) convert MCP tools into tools the tool runner can call.

**c) Server-side tools (Anthropic-hosted).** Enabled per turn with a single tool
entry each — no execution code on our side:

- **Web search** — for questions needing current information.
- **Code execution** — for calculations, data processing, and generating files.

Tool selection is configurable so we can enable only what a deployment needs.
Because adding or removing tools mid-conversation invalidates the prompt cache, we
keep the active tool set stable within a conversation.

**Security:** custom tools with side effects (writes, external calls) validate
their inputs and, where destructive, require confirmation before execution. Tool
inputs are always parsed as JSON, never string-matched.

---

## 6. Data model and storage

| Data | Contents | Prototype | Production |
|---|---|---|---|
| Conversation memory | Per-group (`chat_id`) list of attributed turns | In-process dict | Redis or a small SQL/Postgres table keyed by `chat_id` |
| Knowledge base | Chunk text + embedding + metadata | Local FAISS/Chroma file | pgvector / hosted vector DB |
| Config & secrets | Tokens, keys, model name, chat allowlist | `.env` (dev only) | Host platform environment variables |

Conversation memory must be **durable** in production so a restart or redeploy
does not wipe each group's context. Redis (with TTL-based expiry) is the simplest
fit; Postgres is preferred if we also want analytics/transcripts later. Since the
bot serves only a handful of groups, data volume is small.

---

## 7. Deployment and operations

**Target:** **Railway**, a managed hosting platform. It runs a long-lived process,
restarts it on crash, and requires no server administration. (Render and Fly.io are
equivalent alternatives if we ever need to move.)

**Why managed platform over the alternatives:**

- *vs. a developer laptop:* the laptop approach only works while the machine is on
  and online — unacceptable for a 24/7 bot. It remains the mode for local testing.
- *vs. a self-managed VPS:* a VPS works but adds OS patching, process supervision,
  and TLS management. A managed platform removes that undifferentiated work for a
  single always-on worker.
- *vs. serverless webhooks:* more efficient at high scale, but requires a public
  HTTPS endpoint and a different execution model. Deferred to future work.

**Deployment artifacts:**

- `requirements.txt` — `python-telegram-bot`, `anthropic`, plus embeddings and
  vector-store client libraries.
- Start command — runs the polling worker (e.g. `python bot.py`).
- Environment variables (secrets, never committed):
  `TELEGRAM_TOKEN`, `ANTHROPIC_API_KEY`, the embeddings-provider key,
  `ALLOWED_CHAT_IDS` (the approved group allowlist), `MODEL`, and store
  connection strings.

**Process model:** exactly **one** polling worker instance. Running two pollers
against the same bot token causes Telegram update conflicts. Horizontal scaling,
if ever needed, requires switching to webhooks.

**Observability:** structured logs for each turn (chat_id, latency, token usage,
tools invoked, errors). Log the Anthropic request ID on failures. Track token
usage per day for cost monitoring.

**Failure handling:** the SDK retries transient API errors (429/5xx) with backoff.
On unrecoverable errors we send the user a graceful fallback message rather than
failing silently.

---

## 8. Security and privacy

- **Secrets** live only in the host platform's environment configuration. The bot
  token and API keys are treated as passwords and never committed to source control.
- **Chat allowlist:** the bot only operates in approved group chats (`ALLOWED_CHAT_IDS`).
  Added to any other group, it ignores messages and can auto-leave. This is the
  primary access control.
- **Group data:** conversation history is stored per group. We define a retention
  policy (e.g. expire memory after N days) and honor `/reset` as a delete. Group
  members should be aware the bot retains recent context.
- **Tool safety:** side-effecting tools validate inputs and gate destructive
  actions. MCP server credentials are held server-side, never sent to the model.
- **No secrets in prompts:** API keys and credentials are never placed in system
  prompts or message content.
- **Abuse controls:** optional per-group rate limiting to bound cost and prevent spam.

---

## 9. Cost model

Costs scale with usage, not a fixed platform fee:

- **Claude API** — priced per input/output token. Conversation memory and RAG
  increase input tokens per turn; prompt caching of the stable system prefix
  offsets part of this. Model choice (`opus` vs `sonnet`) is the main cost lever.
- **Embeddings provider** — per token embedded (one-time at ingestion, plus one
  query embedding per turn).
- **Hosting** — a small fixed monthly fee for one always-on worker.

Because the bot serves a limited set of groups and only responds when addressed
(privacy mode), traffic — and therefore cost — is modest and bounded. We monitor
daily token usage and can downshift the model or trim memory/RAG context if spend
exceeds budget.

---

## 10. Testing

- **Local:** run the polling worker on a developer machine against a test bot
  created via BotFather; add it to a test group and exercise it there.
- **Addressing & allowlist:** verify the bot replies when @-mentioned or replied to,
  stays silent for other group chatter, and ignores messages from non-approved chats.
- **Unit tests:** memory trimming, sender attribution, chunking, retrieval
  thresholding, message splitting, and tool-result handling are covered independent
  of the network.
- **Tool loop:** verify multi-step tool conversations terminate and feed results
  back correctly.

---

## 11. Milestones

1. **M1 — Skeleton bot.** BotFather bot (privacy mode on) + polling worker + chat allowlist + basic Claude call. Replies when addressed in an approved test group. Deployed to the managed platform.
2. **M2 — Memory.** Durable per-group conversation history with sender attribution, trimming, and `/reset`.
3. **M3 — Tools.** Custom-function tool loop, then MCP connector, then server tools (web search, code execution).
4. **M4 — Knowledge base.** Ingestion pipeline + retrieval wired into request assembly.
5. **M5 — Hardening.** Rate limiting, retention policy, structured logging, cost dashboards.

---

## 12. Future work

- **Passive listening** — disable privacy mode so the bot follows the whole group conversation, with our own addressing heuristics to decide when to speak.
- **More groups / scale** — grow beyond the current limited set; revisit process model and rate limits if usage climbs.
- **Webhooks** for higher efficiency and horizontal scale.
- **Compaction** of long conversations instead of hard trimming.
- **Vision** — accept and reason over images shared in the group.
- **Analytics / transcripts** dashboard for conversation review.

---

## 13. References

- Telegram Bot API — `https://core.telegram.org/bots/api`
- `python-telegram-bot` — the Telegram library used by the backend.
- Anthropic Claude API docs — model IDs, tool use, MCP connector, prompt caching, streaming.
- Model Context Protocol — `https://modelcontextprotocol.io/`
- Original comparison doc — `design_doc.md` (no-code vs. code approaches; background context).
