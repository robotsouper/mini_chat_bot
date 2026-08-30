"""Telegram bot entrypoint.

Runs a long-polling worker: when someone addresses the bot in an approved chat,
ask Claude for a reply and send it back, keeping a per-chat history so replies
follow the conversation.
"""

from __future__ import annotations

import asyncio
import logging
import re

from telegram import Message, MessageEntity, Update, User
from telegram.constants import ChatAction, ChatType, MessageLimit
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import llm
import memory
from config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Telegram rejects messages longer than this, so replies are split to fit.
MAX_MESSAGE_CHARS = int(MessageLimit.MAX_TEXT_LENGTH)

# A typing action expires after ~5s, so it is re-sent while we wait on Claude.
TYPING_REFRESH_SECONDS = 4.0

# Display names are rendered into the prompt, so cap their length.
MAX_SENDER_CHARS = 64

# Preferred split points for a long reply, coarsest first.
_SPLIT_SEPARATORS = ("\n\n", "\n", " ")


def is_allowed(chat_id: int) -> bool:
    """True if this chat is on the configured allowlist."""
    return chat_id in config.allowed_chat_ids


def is_addressed_to_bot(message: Message, bot_id: int, bot_username: str) -> bool:
    """True if the message is directed at the bot rather than the group at large.

    In a private chat every message is for the bot. In a group it counts as
    addressed when it @-mentions the bot or replies to one of its messages —
    the same two cases Telegram delivers under privacy mode.
    """
    if message.chat.type == ChatType.PRIVATE:
        return True

    replied_to = message.reply_to_message
    if replied_to is not None and replied_to.from_user is not None:
        if replied_to.from_user.id == bot_id:
            return True

    handle = f"@{bot_username}".lower()
    for entity in message.entities:
        if entity.type != MessageEntity.MENTION:
            continue
        try:
            # parse_entity handles Telegram's UTF-16 offsets correctly.
            mentioned = message.parse_entity(entity)
        except (UnicodeDecodeError, IndexError):
            # Malformed offsets: fall back to a plain scan of the text.
            return re.search(rf"@{re.escape(bot_username)}\b", message.text, re.I) is not None
        if mentioned.lower() == handle:
            return True
    return False


def sender_label(user: User | None) -> str:
    """A short display name for whoever sent a message.

    Prefers the display name, falls back to the @username. Collapsed to one
    line and truncated: this string is rendered into the prompt, so a name
    carrying newlines could otherwise forge extra conversation turns.
    """
    if user is None:
        return "Unknown"
    name = user.full_name or (f"@{user.username}" if user.username else "") or "Unknown"
    return re.sub(r"\s+", " ", name).strip()[:MAX_SENDER_CHARS]


def strip_mention(text: str, bot_username: str) -> str:
    """Remove the bot's @handle so the model never sees it."""
    handle = rf"@{re.escape(bot_username)}\b"
    # A mention that opens a line takes its surrounding spaces with it, so
    # "@bot what's up" does not reach the model as " what's up".
    cleaned = re.sub(rf"(?m)^[ \t]*{handle}[ \t]*", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(handle, "", cleaned, flags=re.IGNORECASE)
    # Drop the double spaces left behind by a mid-sentence mention, but leave
    # leading indentation alone so pasted code survives intact.
    return re.sub(r"(?<=\S)[ \t]{2,}", " ", cleaned).strip()


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split a reply into Telegram-sized chunks on the cleanest boundary.

    Prefers paragraph breaks, then line breaks, then spaces, and only slices
    mid-word when a single run of text has no break in it at all.
    """
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []

    for separator in _SPLIT_SEPARATORS:
        if separator not in text:
            continue
        chunks: list[str] = []
        buffer = ""
        for piece in text.split(separator):
            candidate = f"{buffer}{separator}{piece}" if buffer else piece
            if len(candidate) <= limit:
                buffer = candidate
                continue
            if buffer:
                chunks.append(buffer)
                buffer = ""
            if len(piece) > limit:
                # Still too long on its own — retry with a finer separator.
                chunks.extend(split_message(piece, limit))
            else:
                buffer = piece
        if buffer:
            chunks.append(buffer)
        return chunks

    return [text[i : i + limit] for i in range(0, len(text), limit)]


async def _keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Show the typing indicator until cancelled.

    Purely cosmetic, so a failure here is logged and swallowed rather than
    allowed to surface while the real reply is still in flight.
    """
    try:
        while True:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(TYPING_REFRESH_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("Typing indicator failed for chat_id=%s", chat_id, exc_info=True)


async def whereami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Report the current chat's ID so it can be added to the allowlist.

    Deliberately exempt from the allowlist gate: you need this to discover the
    ID of a chat that is not approved yet.
    """
    chat = update.effective_chat
    if chat is None:
        return

    status = "allowed" if is_allowed(chat.id) else "NOT on the allowlist"
    logger.info("/whereami in chat_id=%s (%s)", chat.id, chat.type)
    await update.effective_message.reply_text(
        f"chat_id: {chat.id}\n"
        f"type: {chat.type}\n"
        f"title: {chat.title or chat.full_name or '-'}\n"
        f"status: {status}\n\n"
        f"Add this ID to ALLOWED_CHAT_IDS and restart the bot."
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear this chat's conversation history.

    Gated by the allowlist, unlike `/whereami` — this one acts on stored data
    rather than helping you discover a chat in the first place.
    """
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None:
        return

    if not is_allowed(chat.id):
        logger.info("Ignoring /reset from non-allowlisted chat_id=%s", chat.id)
        return

    dropped = await memory.reset(chat.id)
    logger.info("/reset cleared %d turns in chat_id=%s", dropped, chat.id)
    await message.reply_text(
        "Conversation history cleared." if dropped else "Nothing to forget."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to a message addressed to the bot with an LLM-generated response."""
    message = update.message
    if message is None or not message.text:
        return

    if not is_allowed(message.chat_id):
        logger.info(
            "Ignoring message from non-allowlisted chat_id=%s (%s)",
            message.chat_id,
            message.chat.type,
        )
        return

    bot = context.bot
    if not is_addressed_to_bot(message, bot.id, bot.username):
        return

    text = strip_mention(message.text, bot.username)
    if not text:
        await message.reply_text("Yes? Ask me anything.")
        return

    sender = sender_label(message.from_user)
    history = await memory.load(message.chat_id)

    typing = asyncio.create_task(_keep_typing(context, message.chat_id))
    try:
        reply = await llm.respond(text, history, sender)
    finally:
        typing.cancel()

    # Recorded only after a successful reply, so a failed call leaves no
    # dangling user turn and the stored history keeps alternating cleanly.
    await memory.append(message.chat_id, "user", text, sender)
    await memory.append(message.chat_id, "assistant", reply)

    for chunk in split_message(reply):
        await message.reply_text(chunk)


async def _startup(app: Application) -> None:
    """Open the memory backend once the event loop is running."""
    await memory.init()


async def _shutdown(app: Application) -> None:
    """Release the memory backend's connections on the way out."""
    await memory.close()


def main() -> None:
    app = (
        ApplicationBuilder()
        .token(config.telegram_token)
        .post_init(_startup)
        .post_shutdown(_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("whereami", whereami))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    if config.allowed_chat_ids:
        logger.info("Allowlisted chats: %s", sorted(config.allowed_chat_ids))
    else:
        logger.warning(
            "ALLOWED_CHAT_IDS is empty — the bot will ignore every message. "
            "Send /whereami in a chat to get its ID, then add it."
        )
    logger.info("Bot is running (polling). Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
