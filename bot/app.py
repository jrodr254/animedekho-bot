"""Application factory — builds and configures the Telegram bot."""

import logging

from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters,
)

from config.settings import settings
from utils.http import http_client
from .handlers import cmd_start, cmd_search, cmd_help, callback_router, handle_text

log = logging.getLogger(__name__)


async def _post_init(app):
    """Called after app.initialize() — start HTTP client."""
    await http_client.start()
    log.info("HTTP client started")


async def _post_shutdown(app):
    """Called on shutdown — cleanup."""
    await http_client.close()
    log.info("HTTP client closed")


def create_app():
    """Build the Telegram Application with all handlers registered."""
    if not settings.bot.token:
        raise RuntimeError("BOT_TOKEN environment variable is required")

    app = (
        ApplicationBuilder()
        .token(settings.bot.token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("help", cmd_help))

    # Callback queries (inline buttons)
    app.add_handler(CallbackQueryHandler(callback_router))

    # Text messages (search)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app
