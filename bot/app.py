"""Application factory — builds and configures the Telegram bot."""

import logging

from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters,
)

from config.settings import settings
from utils.http import http_client
from .handlers import (
    cmd_start, cmd_help, callback_router, handle_text,
    cmd_adduser, cmd_removeuser, cmd_users,
    cmd_setlogchannel, cmd_setmainchannel, cmd_setchannellink,
)

log = logging.getLogger(__name__)


async def _post_init(app):
    """Called after app.initialize() — start HTTP client, DB & logger."""
    await http_client.start()
    log.info("HTTP client started")

    # Init MongoDB
    from bot.database import Database
    import bot.database as db_mod
    db = Database(settings.bot.mongo_uri)
    await db.init_indexes()
    db_mod.db = db
    log.info("MongoDB connected")

    # Init bot logger
    from bot.logger import BotLogger
    import bot.logger as logger_mod
    logger_mod.bot_logger = BotLogger(app.bot)
    log.info("Bot logger initialized")

    # Init Library Manager
    from bot.library import LibraryManager
    import bot.library as lib_mod
    bot_username = (await app.bot.get_me()).username or ""
    lib_mod.library_manager = LibraryManager(
        bot=app.bot,
        db=db,
        main_channel=settings.bot.main_channel,
        bot_username=bot_username,
    )
    log.info("Library manager initialized (bot: @%s)", bot_username)


async def _post_shutdown(app):
    """Called on shutdown — cleanup."""
    await http_client.close()
    from bot.database import db
    if db:
        db.close()
    log.info("HTTP client & MongoDB closed")


def create_app():
    """Build the Telegram Application with all handlers registered."""
    if not settings.bot.token:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    if not settings.bot.owner_id:
        raise RuntimeError("OWNER_ID environment variable is required")

    app = (
        ApplicationBuilder()
        .token(settings.bot.token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    # Admin commands (owner-only)
    app.add_handler(CommandHandler("adduser", cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("setlogchannel", cmd_setlogchannel))
    app.add_handler(CommandHandler("setmainchannel", cmd_setmainchannel))
    app.add_handler(CommandHandler("setchannellink", cmd_setchannellink))

    # Callback queries (inline buttons)
    app.add_handler(CallbackQueryHandler(callback_router))

    # Text messages (search)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app
