#!/usr/bin/env python3
"""AnimeDekho Telegram Bot — entrypoint (Pyrogram/MTProto)."""

import logging
import sys

from config.settings import settings
from bot.app import create_app


import asyncio
from pyrogram import idle

async def async_main():
    app = create_app()
    await app.start()
    
    if hasattr(app, "on_start") and callable(app.on_start):
        await app.on_start(app)
        
    await idle()
    
    if hasattr(app, "on_stop") and callable(app.on_stop):
        await app.on_stop(app)
        
    await app.stop()

def main():
    logging.basicConfig(
        level=getattr(logging, settings.bot.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )

    log = logging.getLogger("animedekho")
    log.info("Starting AnimeDekho Bot (Pyrogram/MTProto)...")

    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        log.info("Shutting down...")
    except Exception as e:
        log.critical("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
