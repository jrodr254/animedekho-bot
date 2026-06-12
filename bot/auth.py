"""User authorization — owner-managed approved user list."""

from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import settings

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_USERS_FILE = _DATA_DIR / "users.json"

_approved_users: set[int] = set()


# ── Persistence ────────────────────────────────────────────────────────

def load_users() -> None:
    global _approved_users
    try:
        if _USERS_FILE.exists():
            data = json.loads(_USERS_FILE.read_text())
            _approved_users = set(data.get("approved_users", []))
            log.info("Loaded %d approved users", len(_approved_users))
    except Exception as e:
        log.warning("Failed to load users: %s", e)
        _approved_users = set()


def save_users() -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _USERS_FILE.write_text(json.dumps(
            {"approved_users": sorted(_approved_users)}, indent=2
        ))
    except Exception as e:
        log.warning("Failed to save users: %s", e)


# ── Checks ─────────────────────────────────────────────────────────────

def is_owner(user_id: int) -> bool:
    return user_id == settings.bot.owner_id


def is_approved(user_id: int) -> bool:
    return is_owner(user_id) or user_id in _approved_users


def add_user(user_id: int) -> bool:
    """Add user. Returns True if newly added, False if already present."""
    if user_id in _approved_users:
        return False
    _approved_users.add(user_id)
    save_users()
    return True


def remove_user(user_id: int) -> bool:
    """Remove user. Returns True if removed, False if not found."""
    if user_id not in _approved_users:
        return False
    _approved_users.discard(user_id)
    save_users()
    return True


def get_users() -> list[int]:
    return sorted(_approved_users)


# ── Decorator ──────────────────────────────────────────────────────────

def require_approved(func):
    """Decorator: blocks unapproved users."""
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if not is_approved(user_id):
            target = update.callback_query or update.message
            if target:
                text = "🔒 You don't have access. Ask the owner to add you."
                if hasattr(target, "answer"):
                    await target.answer(text, show_alert=True)
                else:
                    await target.reply_text(text)
            return
        return await func(update, ctx)
    return wrapper


def require_owner(func):
    """Decorator: blocks non-owners."""
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if not is_owner(user_id):
            if update.message:
                await update.message.reply_text("⚠️ This command is owner-only.")
            return
        return await func(update, ctx)
    return wrapper
