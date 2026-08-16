# Telegram Assistant Bot

A general-purpose Claude-powered assistant that lives in a small set of Telegram
group chats. See [telegram_bot_design.md](telegram_bot_design.md) for the design
and [implementation.md](implementation.md) for the task-by-task build plan.

> **Status:** Task 7 — a multi-turn bot that replies via Claude in allowlisted
> chats, only when addressed, with per-chat conversation memory that tracks who
> said what, deployable to Railway as a polling worker.

## Prerequisites

- **Python 3.11+**
- **A Telegram bot token** — create a bot via [@BotFather](https://t.me/BotFather):
  send `/newbot`, choose a name and a username ending in `bot`, and copy the token.
- **An Anthropic API key** — from [console.anthropic.com](https://console.anthropic.com).
  This is the pay-as-you-go **API**, which is separate from a Claude Pro
  subscription (a Pro plan does not grant API access). A few dollars of credit is
  plenty for a small bot.

## Setup

> **Windows note:** make sure the venv uses Python 3.11+. On this machine, plain
> `python` is 3.6.8 (too old); use the launcher `py -3` (or `python3`) to create
> the venv. Once activated, `python` inside the venv points to the right version.

```bash
# 1. Create a virtualenv with Python 3.11+ and install dependencies
py -3 -m venv .venv          # Windows;  macOS/Linux: python3 -m venv .venv
# Windows PowerShell:  .venv\Scripts\Activate.ps1
# macOS/Linux:         source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure secrets
copy .env.example .env       # Windows;  macOS/Linux: cp .env.example .env
# then edit .env and fill in TELEGRAM_TOKEN and ANTHROPIC_API_KEY
```

## Verify the configuration

With the venv activated and `.env` filled in, this should load without error:

```bash
python -c "import config; print('Config OK, model =', config.config.model)"
```

If a required variable is missing, it fails immediately with a clear message
naming the variable.

## Configuration reference

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `TELEGRAM_TOKEN` | yes | — | Bot token from BotFather |
| `ANTHROPIC_API_KEY` | yes | — | Anthropic API key |
| `MODEL` | no | `claude-sonnet-4-6` | Claude model to use |
| `ALLOWED_CHAT_IDS` | no | *(empty)* | Comma-separated chat IDs the bot may operate in. Empty means it ignores every message. |

## Running the bot

With the venv activated and `.env` filled in:

```bash
python bot.py
```

The bot starts long-polling and logs `Bot is running (polling)`. Press `Ctrl+C`
to stop.

## Finding a chat ID (the allowlist)

The bot only answers in chats listed in `ALLOWED_CHAT_IDS`; everywhere else it
stays silent and logs the ignored chat ID. To approve a chat:

1. Start the bot (`python bot.py`) — with an empty allowlist it logs a warning
   and answers nothing, which is expected.
2. In the chat you want to approve (a private chat with the bot, or a group it
   has been added to), send **`/whereami`**. The bot replies with that chat's
   `chat_id` — this command works even in non-approved chats, since it is how
   you discover the ID in the first place.
   *In a group with privacy mode on, send it as `/whereami@yourbotname`.*
   You can also just send any message and read the ID off the console log line:
   `Ignoring message from non-allowlisted chat_id=-1001234567890 (supergroup)`.
3. Put the ID in `.env` and restart the bot:

   ```
   ALLOWED_CHAT_IDS=123456789,-1001234567890
   ```

   Private chat IDs are positive; group and supergroup IDs are negative.

## Using the bot in a group

**BotFather setup (one-time):** keep **group privacy mode ON** — the default.
In [@BotFather](https://t.me/BotFather): `/mybots` → your bot → *Bot Settings* →
*Group Privacy* → it should read *enabled*. With privacy on, Telegram only
delivers the group messages that are actually meant for the bot, so it never
sees ordinary chatter and costs nothing while idle. If you toggle it, remove the
bot from the group and re-add it for the change to take effect.

Then add the bot to the group and put the group's ID in `ALLOWED_CHAT_IDS`
(see above). In the group it answers when you:

- **@-mention it** — `@yourbotname what's the weather like on Mars?`
- **reply to one of its messages** — no mention needed

Anything else is ignored. The bot shows a *typing…* indicator while Claude is
working, and answers longer than Telegram's 4096-character limit arrive as
several messages, split at paragraph breaks. In a private chat every message is
treated as addressed to the bot, so no mention is needed.

## Conversation memory

The bot remembers earlier turns, so follow-ups work — ask "what did I just
ask?" or "explain that differently" and it has the context. Memory is **shared
per chat**, keyed by `chat_id`: everyone in a group contributes to and reads
from one thread, the way a participant would.

Each stored user turn also records **who said it**, taken from the sender's
Telegram display name. The name is folded into the message as `Name: text` when
the request is assembled, so the bot can answer "who asked about X?" and address
people individually. Names are collapsed to one line and truncated before being
rendered, so a display name containing newlines can't forge extra turns.

Two limits for now, both addressed by later tasks:

- **It resets on restart.** History lives in the bot's process, so a redeploy or
  crash clears every chat. Task 9 moves it to Redis.
- **It grows without bound.** Nothing trims old turns yet, so a long-running
  chat sends an ever-larger request each time. Task 8 adds trimming and
  a `/reset` command.

Because a Railway redeploy restarts the process, shipping a change is currently
also a memory wipe for every group.

## Deploying to Railway

The bot is a **worker**: a long-running process that polls Telegram for updates.
It makes only outbound calls, so it needs no port, no domain, and no health
check. [`Procfile`](Procfile) declares it:

```
worker: python bot.py
```

Railway builds the repo (detecting Python from `requirements.txt`, pinned by
[`.python-version`](.python-version)), runs that command, streams stdout/stderr
to the deploy logs, and restarts the process if it exits. `.env` never ships —
it is gitignored, and the container reads its variables from Railway instead,
which `load_dotenv()` in [config.py](config.py) quietly no-ops around.

### Steps

1. **Push the code to GitHub.** Railway deploys from a repo. `.env` is covered by
   [`.gitignore`](.gitignore); secrets go in Railway's variables instead.
2. **Create the service.** On [railway.app](https://railway.app): *New Project* →
   *Deploy from GitHub repo* → pick this repo. The first build starts
   immediately and will crash-loop until step 3 — that is expected.
3. **Set the variables.** Service → *Variables* → add `TELEGRAM_TOKEN`,
   `ANTHROPIC_API_KEY`, `ALLOWED_CHAT_IDS`, and optionally `MODEL`. Same values
   as your local `.env` (`ALLOWED_CHAT_IDS` takes no quotes and no spaces).
   Saving triggers a redeploy.
4. **Check the logs.** Service → *Deployments* → the active deploy should show
   `Allowlisted chats: [...]` followed by `Bot is running (polling)`.
5. **Stop your local bot**, then message it from Telegram. A reply means Railway
   is serving it. Close your laptop and try again to confirm.

### Keeping it running

**Exactly one poller at a time.** Two processes polling the same bot token fight
over updates and both log `Conflict: terminated by other getUpdates request`,
which makes replies vanish at random. So:

- Leave *Settings → Replicas* at **1**. Never scale this service up.
- Don't run `python bot.py` locally while the Railway deploy is live — stop one,
  or register a second BotFather bot to use as a dev token.
- A brief conflict *during* a redeploy is normal while the old container drains;
  it clears within seconds.

**Deploying a change:** push to the tracked branch and Railway rebuilds
automatically.

### Cost

- Railway's Hobby plan is ~$5/month of included usage. A polling worker is tiny
  but runs 24/7, so it draws continuously even while idle.
- Anthropic API usage is billed separately, per token, and will likely be the
  larger line item once the bot is in daily use.
- No volume or database is needed yet. Conversation memory is still in-process,
  so **every redeploy or restart wipes it** — Task 9 fixes that with Redis.
