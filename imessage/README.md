# iMessage adapter

**Status: not started.** This directory currently holds the research that should
shape the implementation, so the decisions are written down before any code
commits us to one of them.

## The constraint everything follows from

iMessage has no public API. Telegram hands you a bot token and long polling;
Apple offers nothing equivalent for personal conversations. Every working
integration therefore reduces to one sentence:

> Something, somewhere, must run a real Messages client signed in to an Apple ID.

End-to-end encryption protects messages *in transit*. On a signed-in device they
are decrypted at rest — which is the opening every approach below exploits. The
routes differ only in **who operates that device**.

## Routes considered

### A. Self-hosted Mac

Read `~/Library/Messages/chat.db` (plain SQLite) for incoming messages; send by
driving Messages.app with AppleScript.

Practical costs, in rough order of how quickly they bite:

- Needs **Full Disk Access**, which grants the process every message you have
  ever received — not just the ones meant for the bot.
- The `text` column is increasingly `NULL`; since Ventura the body often lives in
  `attributedBody` as an archived `NSAttributedString` (typedstream) that must be
  decoded. This broke most older tutorials.
- Timestamps use the Apple epoch (2001-01-01), in nanoseconds since High Sierra.
- Writing to `chat.db` does **not** send anything — it is a local cache. Sending
  must go through the app, which needs Automation (and sometimes Accessibility)
  permission, and Apple keeps tightening it.

### B. Hosted relay

Someone else runs route A and exposes a normal API. Self-hosted bridges
(BlueBubbles, AirMessage) still require your own always-on Mac; commercial
relays do not, because they operate the Apple-side infrastructure themselves.

This is the route documented in `AI接入iMessage.pdf` (kept locally, gitignored),
which uses **Photon** + the `spectrum-ts` Node SDK:

```
iPhone ──iMessage──▶ Photon relay ──▶ your VPS ──▶ LLM
```

Photon assigns each project its own number. Routing binds to whichever number
first contacted the project, so **you must text it once from your phone** before
it can reach you — the free tier rejects cold outbound with
`Target not allowed for this project`.

### C. Protocol reverse engineering

Re-implement APNs + IDS registration to act as an iMessage client with no Apple
hardware. Technically possible (Beeper Mini), repeatedly blocked by Apple, and a
good way to get an Apple ID restricted. Not a basis for anything we want to keep
running.

### D. Apple Messages for Business

The only officially sanctioned path: verified businesses receive customer-initiated
conversations via webhook. Clean architecture, but it cannot touch personal chats,
so it does not solve this problem.

## Leaning, and what is still undecided

**Route B is the realistic option** — it is the only one that avoids owning a Mac
without also violating Apple's terms outright. It is not yet chosen, because it
trades that convenience for two things worth being explicit about:

- **A third party sees the plaintext.** The relay has to decrypt in order to
  speak Apple's protocol on your behalf, so private conversations now traverse
  Photon *in addition to* whatever LLM endpoint we use. That is strictly more
  exposure than the Telegram bot, which only ever sees messages that @-mention it.
- **The bot is you.** There is no separate bot account. It sends as your Apple
  ID, so a misfire is you misfiring, and an account restriction is your account.
  An allowlist is therefore not a nicety here — without one the assistant will
  cheerfully reply to your family.

## What carries over from `telegram/`

The core is already platform-agnostic and should not be rewritten:

| Concern | Reuse |
|---|---|
| Persona / system prompt | `telegram/llm.py` as-is |
| Conversation history | `telegram/memory.py` — swap `chat_id` for the iMessage chat GUID; the Postgres schema needs no change |
| History trimming, token budget | unchanged |
| Sender attribution | unchanged; only the source of the display name differs |
| "Exactly one connection" rule | the same constraint, for the same reason |

What must be newly written is only the edge: receiving, deciding whether a
message is addressed to the bot, and sending.

## Open questions to settle before writing code

1. **Language.** `spectrum-ts` is a Node/TypeScript SDK. Does Photon expose a
   plain HTTP or webhook interface? If so the existing Python core is reused
   directly behind a thin adapter. If not, the choice is a TS rewrite of the edge
   or a small Node sidecar that forwards to the Python service.
2. **Cost and terms** of the relay at the volume we would actually use.
3. **Whether the privacy trade above is acceptable** for real personal chats.

## Design ideas worth borrowing

The source document solves "make it feel human" without tool use, by having the
model emit **inline markers** that the sender parses:

| Marker | Effect |
|---|---|
| `[voice]text` | send as audio instead of text |
| `[img:label]` | send a sticker, resolved through a label → URL table |
| `[delay:15]text` | send this line 15 minutes later |

Replies are split on newlines and sent as separate messages ~700 ms apart, which
reads like a person typing several times. It is a poor-man's tool use: any
instruction-following model can do it, at the cost of occasionally leaking a
malformed marker into the visible text.

## Known limitations of route B (from the source document)

- **Native voice bubbles are broken** on the relay — `voice()` produces an
  unplayable 00:00 bubble. The workaround is a generic file attachment, which
  plays but renders as a file box rather than a waveform. Described as a ceiling,
  not a misconfiguration.
- **Delayed messages are not durable.** The reference implementation uses an
  in-memory `setTimeout`, so a restart drops the queue. We would put them in
  Postgres next to `turns` instead — the same fix we already applied to memory.
- **One connection per set of credentials.** Opening a second client with the
  same project id sends fine but silently stops receiving. Any side process must
  route through the main service rather than connecting on its own.
