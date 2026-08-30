"""Conversation memory.

Holds a short history of turns per chat so the bot stays coherent across
messages instead of answering each one in isolation. Memory is shared per
group — one thread per `chat_id`, not per person — so the bot follows a group
conversation the way a participant would.

This is an in-process store: it resets whenever the bot restarts, which is fine
for now. Task 9 swaps in Redis behind this same `load`/`append` interface.
"""

from __future__ import annotations

# One stored turn: `role` and `content`, plus `sender` on user turns naming who
# spoke. Note this is NOT the API's message shape — the API rejects unknown
# keys, so `llm` renders these into `role`/`content` pairs before sending.
Turn = dict[str, str]

# Turns kept per chat. Every request resends the whole history, so an untrimmed
# chat costs more on each message than the one before it. Two turns make one
# exchange, so this is roughly 20 back-and-forths.
MAX_TURNS = 40

_store: dict[int, list[Turn]] = {}


def load(chat_id: int) -> list[Turn]:
    """Return a chat's turns, oldest first.

    Returns a copy, so a caller mutating the result can't corrupt the store.
    """
    return list(_store.get(chat_id, []))


def append(chat_id: int, role: str, content: str, sender: str | None = None) -> None:
    """Record one turn against a chat.

    `sender` names who wrote a user turn, so the model can tell a group's
    speakers apart. Turns are appended strictly in user/assistant pairs (see
    `bot.handle_text`), keeping the stored history in the order the API expects.
    """
    turn = {"role": role, "content": content}
    if sender:
        turn["sender"] = sender

    turns = _store.setdefault(chat_id, [])
    turns.append(turn)

    if len(turns) > MAX_TURNS:
        del turns[: len(turns) - MAX_TURNS]
        # The API requires the first message to come from the user, so drop an
        # assistant turn left stranded at the front by the cut.
        if turns[0]["role"] != "user":
            del turns[0]


def reset(chat_id: int) -> int:
    """Forget a chat's history, returning how many turns were dropped.

    Other chats are untouched — the store is keyed per `chat_id`.
    """
    return len(_store.pop(chat_id, []))
