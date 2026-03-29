"""OpenSuzy — Session persistence.

Maps chat_id → Claude session_id for conversation continuity.
Stored as JSON on disk, loaded on startup.
"""

import asyncio
import copy
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from src import config

logger = logging.getLogger(__name__)

_sessions: dict = {}
_lock = asyncio.Lock()

SESSION_EXPIRY_DAYS = 7
SESSION_MAX_MESSAGES = 8  # auto-rotate after this many messages to prevent context bloat
# (lowered from 15 — tool-heavy sessions like email/todo fill context fast,
#  and extended thinking on Sonnet 4.6 needs room in the context window)


def _sessions_path() -> Path:
    return config.SESSIONS_FILE


def load():
    """Load sessions from disk on startup."""
    global _sessions
    path = _sessions_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                _sessions = json.load(f)
            logger.info(f"Loaded {len(_sessions)} sessions from {path}")
        except Exception as e:
            logger.error(f"Failed to load sessions: {e}")
            _sessions = {}
    else:
        _sessions = {}


def _atomic_write(path: str, data: str):
    """Write data atomically using temp file + rename."""
    dir_name = os.path.dirname(path) or '.'
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(data)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _save_sync(snapshot: dict):
    """Save sessions to disk (blocking I/O — call via run_in_executor).

    Args:
        snapshot: A deep copy of _sessions taken before dispatching to the executor,
                  so the dict cannot mutate mid-serialization.
    """
    path = _sessions_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(str(path), json.dumps(snapshot, indent=2))
    except Exception as e:
        logger.error(f"Failed to save sessions: {e}")


async def _save():
    """Save sessions to disk (non-blocking — runs I/O in executor)."""
    snapshot = copy.deepcopy(_sessions)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _save_sync, snapshot)


_soul_mtime_cache: tuple[float, float] = (0.0, 0.0)  # (computed_at, value)
_SOUL_MTIME_TTL = 5.0  # seconds


def _soul_files_mtime() -> float:
    """Return the latest modification time across all soul/ files (cached 5s)."""
    global _soul_mtime_cache
    now = time.time()
    if now - _soul_mtime_cache[0] < _SOUL_MTIME_TTL:
        return _soul_mtime_cache[1]

    soul_dir = config.SOUL_DIR
    mtimes = []
    for filename in ["SOUL.md", "IDENTITY.md", "USER.md", "AGENTS.md", "TOOLS.md", "TASKS.md", "EMAIL.md", "CALENDAR.md"]:
        p = soul_dir / filename
        try:
            if p.exists():
                mtimes.append(p.stat().st_mtime)
        except OSError:
            pass
    result = max(mtimes) if mtimes else 0.0
    _soul_mtime_cache = (now, result)
    return result


async def get(chat_id: str) -> Optional[str]:
    """Get session_id for a chat. Returns None if no session or expired."""
    async with _lock:
        entry = _sessions.get(chat_id)
        if not entry:
            return None

        # Check expiry
        last_active = entry.get("last_active", 0)
        if time.time() - last_active > SESSION_EXPIRY_DAYS * 86400:
            logger.info(f"Session expired for {chat_id[:20]}...")
            del _sessions[chat_id]
            await _save()
            return None

        # Auto-rotate: too many messages = bloated context
        msg_count = entry.get("message_count", 0)
        if msg_count >= SESSION_MAX_MESSAGES:
            logger.info(f"Session auto-rotated for {chat_id[:20]}... ({msg_count} messages)")
            del _sessions[chat_id]
            await _save()
            return None

        # Invalidate if soul files changed since session was created
        # (ensures updated TOOLS.md etc. take effect immediately)
        session_soul_mtime = entry.get("soul_mtime", 0)
        current_soul_mtime = _soul_files_mtime()
        if session_soul_mtime == 0 or current_soul_mtime > session_soul_mtime:
            logger.info(f"Session invalidated for {chat_id[:20]}... (soul files changed)")
            del _sessions[chat_id]
            await _save()
            return None

        return entry.get("session_id")


async def save(chat_id: str, session_id: str):
    """Save or update session for a chat."""
    async with _lock:
        existing = _sessions.get(chat_id, {})
        _sessions[chat_id] = {
            "session_id": session_id,
            "last_active": time.time(),
            "message_count": existing.get("message_count", 0) + 1,
            "soul_mtime": existing.get("soul_mtime") or _soul_files_mtime(),
        }
        await _save()


async def reset(chat_id: str):
    """Remove session for a chat (forces new conversation)."""
    async with _lock:
        if chat_id in _sessions:
            del _sessions[chat_id]
            await _save()
            logger.info(f"Session reset for {chat_id[:20]}...")


def count() -> int:
    """Number of active sessions."""
    return len(_sessions)
