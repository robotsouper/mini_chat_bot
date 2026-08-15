"""Claude client wrapper.

Owns the Anthropic client and turns a piece of user text into a reply string.
Kept deliberately small for now — memory, tools, and retrieval are added in
later tasks. Uses the async client so calls don't block the Telegram event loop.
"""

from __future__ import annotations

import anthropic

from config import config

SYSTEM_PROMPT = (
    "You are a friendly, concise assistant helping people in a Telegram chat. "
    "Answer directly and keep replies short unless more detail is asked for."
)

# Max tokens for a single reply. Chat answers are short; raised later for
# tool-heavy turns.
MAX_TOKENS = 1024

_client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)


async def respond(text: str) -> str:
    """Send `text` to Claude and return the reply as a plain string."""
    message = await _client.messages.create(
        model=config.model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )

    # A response may contain several content blocks; concatenate the text ones.
    parts = [block.text for block in message.content if block.type == "text"]
    return "".join(parts).strip() or "(no response)"
