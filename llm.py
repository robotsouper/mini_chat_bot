"""Claude client wrapper.

Owns the Anthropic client and turns a piece of user text into a reply string.
Kept deliberately small for now — tools and retrieval are added in later tasks.
Uses the async client so calls don't block the Telegram event loop.
"""

from __future__ import annotations

import anthropic

from config import config
from memory import Turn

SYSTEM_PROMPT = (
    "You are a friendly, concise assistant helping people in a Telegram chat. "
    "Answer directly and keep replies short unless more detail is asked for."
)

# Max tokens for a single reply. Chat answers are short; raised later for
# tool-heavy turns.
MAX_TOKENS = 1024

_client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)


async def respond(text: str, history: list[Turn] | None = None) -> str:
    """Send `text` to Claude and return the reply as a plain string.

    `history` is the chat's earlier turns, oldest first; it is sent ahead of the
    new message so the model can answer follow-ups that depend on context.
    """
    messages = [*(history or []), {"role": "user", "content": text}]

    message = await _client.messages.create(
        model=config.model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=messages,
    )

    # A response may contain several content blocks; concatenate the text ones.
    parts = [block.text for block in message.content if block.type == "text"]
    return "".join(parts).strip() or "(no response)"
