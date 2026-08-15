"""Conversation memory.

Holds a short history of turns per chat so the bot stays coherent across
messages instead of answering each one in isolation. Memory is shared per
group — one thread per `chat_id`, not per person — so the bot follows a group
conversation the way a participant would.

This is an in-process store: it resets whenever the bot restarts, which is fine
for now. Task 9 swaps in Redis behind this same `load`/`append` interface.
"""

from __future__ import annotations

# One stored turn, in the shape the Anthropic API expects.
Turn = dict[str, str]

_store: dict[int, list[Turn]] = {}


def load(chat_id: int) -> list[Turn]:
    """Return a chat's turns, oldest first.

    Returns a copy, so a caller mutating the result can't corrupt the store.
    """
    return list(_store.get(chat_id, []))


def append(chat_id: int, role: str, content: str) -> None:
    """Record one turn against a chat.

    Turns are appended strictly in user/assistant pairs (see `bot.handle_text`),
    which keeps the stored history in the alternating order the API expects.
    """
    _store.setdefault(chat_id, []).append({"role": role, "content": content})
