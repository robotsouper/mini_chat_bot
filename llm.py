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
# character does the work: the model already knows JARVIS, so a few tokens buy
# the whole persona that a description would spend hundreds on.
SYSTEM_PROMPT = (
    "You are JARVIS from Iron Man, in a Telegram group chat. "
    "Address the user as sir. Dry, understated British wit; never effusive. "
    "Answer in one or two sentences unless more is asked for."
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
