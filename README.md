# JARVIS

A Claude-powered assistant that lives in messaging apps. One core, several front
ends.

## Layout

| Directory | Platform | Status |
|---|---|---|
| [telegram/](telegram/) | Telegram groups and DMs | **Live** — deployed on Railway as a polling worker |
| [imessage/](imessage/) | iMessage | Planned — see [imessage/README.md](imessage/README.md) |

## Why one repository

Most of this bot is not platform-specific:

- **The persona.** One system prompt defines JARVIS wherever it speaks.
- **Conversation memory.** Per-chat history in Postgres, keyed by a chat id —
  every platform has some equivalent (a Telegram `chat_id`, an iMessage chat
  GUID).
- **Request assembly.** Sender attribution, history trimming, the token budget
  that follows from resending history on every request.

Only the edges differ: how a message arrives, how you tell "addressed to the
bot" from ordinary group chatter, and how a reply is sent. Keeping the platforms
together keeps that shared core in one place instead of forking it per app.

The split is deliberate rather than incidental — if a change belongs in more than
one platform directory, it probably belongs in the core instead.

## Deployment note

Railway resolves `Procfile` and `requirements.txt` **from the repository root**.
The bot now lives in `telegram/`, so the Railway service must set its
**Root Directory** to `telegram`. Change that setting *before* pushing a
restructure, or the next deploy fails to find its start command.

Each platform is expected to deploy as its own service. Only one process may poll
a given Telegram bot token at a time — two will fight over `getUpdates` and drop
replies at random.

## Documents

- [telegram/telegram_bot_design.md](telegram/telegram_bot_design.md) — the design
- [telegram/implementation.md](telegram/implementation.md) — the task-by-task build plan
- [telegram/README.md](telegram/README.md) — setup, running, and deploying the Telegram bot

## Secrets

No `.env` file is ever committed. Each platform directory keeps its own, ignored
by that directory's `.gitignore`, with a checked-in `.env.example` documenting the
variables. Production values live in the host's dashboard.
