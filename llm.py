"""Claude client wrapper.

Owns the Anthropic client and turns a piece of user text into a reply string.
Kept deliberately small for now — tools and retrieval are added in later tasks.
Uses the async client so calls don't block the Telegram event loop.
"""

from __future__ import annotations

import anthropic

from config import config
from memory import Turn

# Sent on every request, so every token here is a recurring cost. Naming the
# character does the work: the model already knows J.A.R.V.I.S, so a few tokens buy
# the whole persona that a description would spend hundreds on.
SYSTEM_PROMPT = (
    "用钢铁侠的贾维斯的语气和我说话，包括称谓以及说话风格，用中文回答"
)

# Max tokens for a single reply. Chat answers are short; raised later for
# tool-heavy turns.
MAX_TOKENS = 1024

_client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)


def _to_api_turn(turn: Turn) -> dict[str, str]:
    """Render a stored turn into the `role`/`content` pair the API accepts.

    A user turn's sender is folded into the text as `Name: message`, since the
    API has nowhere else to put it — extra keys are rejected.
    """
    sender = turn.get("sender")
    content = f"{sender}: {turn['content']}" if sender else turn["content"]
    return {"role": turn["role"], "content": content}


async def respond(
    text: str,
    history: list[Turn] | None = None,
    sender: str | None = None,
) -> str:
    """Send `text` to Claude and return the reply as a plain string.

    `history` is the chat's earlier turns, oldest first; it is sent ahead of the
    new message so the model can answer follow-ups that depend on context.
    `sender` names whoever wrote `text`, so the model can tell speakers apart.
    """
    messages = [
        *(_to_api_turn(turn) for turn in history or []),
        _to_api_turn({"role": "user", "content": text, "sender": sender or ""}),
    ]

    message = await _client.messages.create(
        model=config.model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=messages,
    )

    # A response may contain several content blocks; concatenate the text ones.
    parts = [block.text for block in message.content if block.type == "text"]
    return "".join(parts).strip() or "(no response)"
