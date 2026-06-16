"""Application factory — builds and configures the Pyrogram bot."""

import logging

from pyrogram import Client

from config.settings import settings

log = logging.getLogger(__name__)


async def _on_start(client: Client):
    """Called after client starts — init HTTP client, DB & logger."""
    from utils.http import http_client
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
    logger_mod.bot_logger = BotLogger(client)
    log.info("Bot logger initialized")

    # Init Library Manager
    from bot.library import LibraryManager
    import bot.library as lib_mod
    me = await client.get_me()
    bot_username = me.username or ""
    lib_mod.library_manager = LibraryManager(
        client=client,
        db=db,
        main_channel=settings.bot.main_channel,
        bot_username=bot_username,
    )
    log.info("Library manager initialized (bot: @%s)", bot_username)

    # Resolve channel peers so Pyrogram can send to them
    for name, cid in [("main", settings.bot.main_channel), ("log", settings.bot.log_channel)]:
        if cid:
            try:
                chat = await client.get_chat(cid)
                log.info("Resolved %s channel: %s (id: %d)", name, chat.title, chat.id)
            except Exception as e:
                log.warning("Could not resolve %s channel %d: %s", name, cid, e)
    # Set bot commands menu
    from pyrogram.types import BotCommand, BotCommandScopeChat
    try:
        # Default commands for everyone
        await client.set_bot_commands([
            BotCommand("start", "Main menu & Search"),
            BotCommand("help", "Show help message"),
        ])
        # Owner commands
        await client.set_bot_commands([
            BotCommand("start", "Main menu & Search"),
            BotCommand("help", "Show help message"),
            BotCommand("adduser", "Approve a user"),
            BotCommand("removeuser", "Remove a user"),
            BotCommand("users", "List approved users"),
            BotCommand("setchannellink", "Set channel invite link"),
            BotCommand("delete", "Delete a series or file"),
        ], scope=BotCommandScopeChat(settings.bot.owner_id))
        log.info("Bot commands set successfully")
    except Exception as e:
        log.warning("Failed to set bot commands: %s", e)
async def _on_stop(client: Client):
    """Called on shutdown — cleanup."""
    from utils.http import http_client
    await http_client.close()
    from bot.database import db
    if db:
        db.close()
    log.info("HTTP client & MongoDB closed")


def create_app() -> Client:
    """Build the Pyrogram Client with all handlers registered."""
    if not settings.bot.token:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    if not settings.bot.owner_id:
        raise RuntimeError("OWNER_ID environment variable is required")
    if not settings.bot.api_id:
        raise RuntimeError("API_ID environment variable is required")
    if not settings.bot.api_hash:
        raise RuntimeError("API_HASH environment variable is required")

    app = Client(
        "animedekho_bot",
        api_id=settings.bot.api_id,
        api_hash=settings.bot.api_hash,
        bot_token=settings.bot.token,
    )

    # Register startup/shutdown hooks
    app.on_start = _on_start
    app.on_stop = _on_stop

    # Register all handlers
    from bot.handlers import register_handlers
    register_handlers(app)

    return app
