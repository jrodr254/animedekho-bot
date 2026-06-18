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
    # Try get_chat first, fall back to raw API (needed on fresh sessions)
    for name, cid in [("main", settings.bot.main_channel), ("log", settings.bot.log_channel)]:
        if cid:
            try:
                chat = await client.get_chat(cid)
                log.info("Resolved %s channel: %s (id: %d)", name, chat.title, chat.id)
            except Exception:
                # Raw API fallback for fresh sessions without cached peers
                try:
                    from pyrogram.raw.functions.channels import GetChannels
                    from pyrogram.raw.types import InputChannel
                    raw_id = abs(cid) % (10 ** 10)  # Strip -100 prefix
                    peer = InputChannel(channel_id=raw_id, access_hash=0)
                    result = await client.invoke(GetChannels(id=[peer]))
                    if result.chats:
                        log.info("Resolved %s channel via raw API: %s", name, result.chats[0].title)
                    else:
                        log.warning("Could not resolve %s channel %d via raw API", name, cid)
                except Exception as e2:
                    log.warning("Could not resolve %s channel %d: %s", name, cid, e2)
    # Auto-generate channel invite link if not set
    if settings.bot.main_channel:
        try:
            from bot.database import db as app_db
            existing_link = await app_db.get_config("channel_invite_link") if app_db else None
            if not existing_link:
                chat = await client.get_chat(settings.bot.main_channel)
                if chat.invite_link:
                    invite_link = chat.invite_link
                else:
                    invite_link = (await client.create_chat_invite_link(settings.bot.main_channel)).invite_link
                if invite_link and app_db:
                    await app_db.set_config("channel_invite_link", invite_link)
                    log.info("Auto-set channel invite link: %s", invite_link)
        except Exception as e:
            log.warning("Could not auto-generate invite link: %s", e)

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
