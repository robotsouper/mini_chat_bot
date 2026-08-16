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
    _store.setdefault(chat_id, []).append(turn)
