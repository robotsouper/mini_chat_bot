"""Configuration loading.

Reads settings from environment variables (and a local `.env` file in
development). Fails fast with a clear error if a required variable is missing,
so misconfiguration is caught at startup rather than mid-request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load a local .env if present. In production (Railway) the variables are set
# in the platform dashboard and this call is a harmless no-op.
load_dotenv()


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in (or set it in your host)."
        )
    return value


def _parse_chat_ids(name: str) -> frozenset[int]:
    """Parse a comma-separated list of chat IDs into a set of ints.

    Unset or empty means "no chats approved" — the bot stays silent everywhere
    (see `/whereami` in bot.py for how to discover a chat's ID).
    """
    raw = os.getenv(name, "")
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise ConfigError(
                f"Invalid entry {part!r} in {name}: expected comma-separated "
                f"integer chat IDs (group IDs are negative, e.g. -1001234567890)."
            ) from None
    return frozenset(ids)


@dataclass(frozen=True)
class Config:
    telegram_token: str
    anthropic_api_key: str
    model: str
    allowed_chat_ids: frozenset[int]
    database_url: str | None


def load_config() -> Config:
    """Build the Config from the environment, raising ConfigError on gaps."""
    return Config(
        telegram_token=_require("TELEGRAM_TOKEN"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        model=os.getenv("MODEL", "claude-sonnet-4-6"),
        allowed_chat_ids=_parse_chat_ids("ALLOWED_CHAT_IDS"),
        # Optional: without it, memory falls back to an in-process store that
        # is wiped on restart (see memory.init).
        database_url=os.getenv("DATABASE_URL") or None,
    )


# Loaded once on import so the whole app shares one config instance.
config = load_config()
