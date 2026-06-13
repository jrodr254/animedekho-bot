from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler, CallbackQueryHandler

from .commands import cmd_start, cmd_help
from .callbacks import callback_router
from .messages import handle_text
from .admin import (
    cmd_adduser, cmd_removeuser, cmd_users,
    cmd_setlogchannel, cmd_setmainchannel, cmd_setchannellink,
)

__all__ = [
    "cmd_start", "cmd_help", "callback_router", "handle_text",
    "cmd_adduser", "cmd_removeuser", "cmd_users",
    "cmd_setlogchannel", "cmd_setmainchannel", "cmd_setchannellink",
    "register_handlers",
]


def register_handlers(app: Client):
    """Register all handlers on the Pyrogram Client."""
    # Commands
    app.add_handler(MessageHandler(cmd_start, filters.command("start") & filters.private))
    app.add_handler(MessageHandler(cmd_help, filters.command("help") & filters.private))

    # Admin commands (owner-only, checked inside each handler)
    app.add_handler(MessageHandler(cmd_adduser, filters.command("adduser") & filters.private))
    app.add_handler(MessageHandler(cmd_removeuser, filters.command("removeuser") & filters.private))
    app.add_handler(MessageHandler(cmd_users, filters.command("users") & filters.private))
    app.add_handler(MessageHandler(cmd_setlogchannel, filters.command("setlogchannel") & filters.private))
    app.add_handler(MessageHandler(cmd_setmainchannel, filters.command("setmainchannel") & filters.private))
    app.add_handler(MessageHandler(cmd_setchannellink, filters.command("setchannellink") & filters.private))

    # Callback queries (inline buttons)
    app.add_handler(CallbackQueryHandler(callback_router))

    # Text messages (search) — must be last to avoid catching commands
    # Note: filters.regex matches non-command text (doesn't start with /)
    app.add_handler(MessageHandler(handle_text, filters.text & filters.private & filters.regex(r"^[^/]")))
