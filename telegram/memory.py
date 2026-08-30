"""Conversation memory.

Holds a short history of turns per chat so the bot stays coherent across
messages instead of answering each one in isolation. Memory is shared per
group — one thread per `chat_id`, not per person — so the bot follows a group
conversation the way a participant would.

Backed by Postgres, so a restart or redeploy no longer wipes context. When
`DATABASE_URL` is unset the store falls back to an in-process dict: local
development works without a database, at the cost of forgetting everything on
exit. `init()` logs loudly which of the two is in use.
"""

from __future__ import annotations

import logging

import asyncpg

from config import config

logger = logging.getLogger(__name__)

# One stored turn: `role` and `content`, plus `sender` on user turns naming who
# spoke. Note this is NOT the API's message shape — the API rejects unknown
# keys, so `llm` renders these into `role`/`content` pairs before sending.
Turn = dict[str, str]

# Turns kept per chat. Every request resends the whole history, so an untrimmed
# chat costs more on each message than the one before it. Two turns make one
# exchange, so this is roughly 20 back-and-forths.
MAX_TURNS = 40

# `id` doubles as the ordering key: rows are inserted one at a time from a
# single worker, so a higher id is always a later turn. `created_at` is unused
# for now and exists for the retention policy in Task 17.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id         BIGSERIAL   PRIMARY KEY,
    chat_id    BIGINT      NOT NULL,
    role       TEXT        NOT NULL,
    content    TEXT        NOT NULL,
    sender     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS turns_chat_id_id_idx ON turns (chat_id, id);
"""

_pool: asyncpg.Pool | None = None

# Used only when no DATABASE_URL is configured.
_fallback: dict[int, list[Turn]] = {}


def _make_turn(role: str, content: str, sender: str | None) -> Turn:
    turn = {"role": role, "content": content}
    if sender:
        turn["sender"] = sender
    return turn


async def init() -> None:
    """Open the connection pool and ensure the schema exists.

    Called once at startup. The pool is small on purpose: a single polling
    worker handles one message at a time, so a handful of connections is
    plenty and leaves headroom under Postgres' connection limit.
    """
    global _pool
    if not config.database_url:
        logger.warning(
            "DATABASE_URL is not set — conversation memory is in-process and "
            "will be lost on restart. Set it to make memory durable."
        )
        return

    _pool = await asyncpg.create_pool(config.database_url, min_size=1, max_size=5)
    async with _pool.acquire() as conn:
        await conn.execute(_SCHEMA)
    logger.info("Conversation memory is backed by Postgres (max %d turns/chat).", MAX_TURNS)


async def close() -> None:
    """Release the pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def load(chat_id: int) -> list[Turn]:
    """Return a chat's turns, oldest first, capped at `MAX_TURNS`."""
    if _pool is None:
        turns = list(_fallback.get(chat_id, []))
    else:
        rows = await _pool.fetch(
            "SELECT role, content, sender FROM turns "
            "WHERE chat_id = $1 ORDER BY id DESC LIMIT $2",
            chat_id,
            MAX_TURNS,
        )
        # Fetched newest-first so the LIMIT keeps the *recent* turns; flip back
        # to chronological order, which is what the API expects.
        turns = [
            _make_turn(row["role"], row["content"], row["sender"])
            for row in reversed(rows)
        ]

    # The API requires the first message to come from the user, so drop any
    # assistant turn left stranded at the front by trimming. Checked on read
    # rather than on write, so a history truncated by any means still loads.
    while turns and turns[0]["role"] != "user":
        del turns[0]
    return turns


async def append(
    chat_id: int, role: str, content: str, sender: str | None = None
) -> None:
    """Record one turn against a chat, trimming the chat back to `MAX_TURNS`.

    `sender` names who wrote a user turn, so the model can tell a group's
    speakers apart. Turns are appended strictly in user/assistant pairs (see
    `bot.handle_text`), keeping the stored history in the order the API expects.
    """
    if _pool is None:
        turns = _fallback.setdefault(chat_id, [])
        turns.append(_make_turn(role, content, sender))
        del turns[: max(0, len(turns) - MAX_TURNS)]
        return

    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO turns (chat_id, role, content, sender) "
                "VALUES ($1, $2, $3, $4)",
                chat_id,
                role,
                content,
                sender or None,
            )
            # Trim on every write so a chat's rows — and the tokens each of its
            # requests costs — stay bounded. Scoped to this chat_id, so a busy
            # group never evicts a quiet one.
            await conn.execute(
                "DELETE FROM turns WHERE chat_id = $1 AND id NOT IN ("
                "  SELECT id FROM turns WHERE chat_id = $1 ORDER BY id DESC LIMIT $2"
                ")",
                chat_id,
                MAX_TURNS,
            )


async def reset(chat_id: int) -> int:
    """Forget a chat's history, returning how many turns were dropped.

    Other chats are untouched — rows are scoped by `chat_id`.
    """
    if _pool is None:
        return len(_fallback.pop(chat_id, []))

    # asyncpg returns the command tag, e.g. "DELETE 12".
    status = await _pool.execute("DELETE FROM turns WHERE chat_id = $1", chat_id)
    return int(status.rpartition(" ")[2] or 0)
