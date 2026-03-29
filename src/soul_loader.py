"""SuzyCloud — Soul context loader.

Loads per-persona soul files and memory into a context string for Claude.
Each persona has its own soul/ and memory/ directories under personas/{key}/.
"""

import logging
from datetime import timedelta
from pathlib import Path
from typing import Optional

from src import config
from src.persona import PersonaConfig, get_persona

logger = logging.getLogger(__name__)

# Per-persona soul cache: persona_key → (content, mtime)
_soul_cache: dict[str, tuple[str, float]] = {}

_SOUL_FILES = [
    "SOUL.md", "IDENTITY.md", "USER.md", "TOOLS.md",
    "TASKS.md", "EMAIL.md", "CALENDAR.md",
]


def _read_file(path: Path) -> str:
    """Read a file, return contents or empty string."""
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to read {path}: {e}")
    return ""


def _soul_mtime(soul_dir: Path) -> float:
    """Latest mtime across soul files in a directory."""
    mtimes = []
    for filename in _SOUL_FILES:
        p = soul_dir / filename
        try:
            if p.exists():
                mtimes.append(p.stat().st_mtime)
        except OSError:
            pass
    return max(mtimes) if mtimes else 0.0


def _load_soul_files(soul_dir: Path, persona_key: str) -> str:
    """Load soul files with per-persona caching."""
    mtime = _soul_mtime(soul_dir)

    cached = _soul_cache.get(persona_key)
    if cached:
        content, cached_mtime = cached
        if mtime <= cached_mtime:
            return content

    parts = []
    for filename in _SOUL_FILES:
        content = _read_file(soul_dir / filename)
        if content:
            parts.append(f"--- {filename} ---\n{content}")

    result = "\n\n".join(parts)
    _soul_cache[persona_key] = (result, mtime)
    logger.debug(f"Soul cache refreshed for {persona_key}: {len(result)} chars")
    return result


def load_soul_context(persona: PersonaConfig, message_text: str = "") -> str:
    """Assemble full soul + memory context for a persona.

    This is what gets piped to Claude as the system context.
    """
    parts = []

    # Soul files (cached per-persona)
    soul_content = _load_soul_files(persona.soul_dir, persona.key)
    if soul_content:
        parts.append(soul_content)

    # Memory files
    for filename in ["MEMORY.md", "projects.md", "contacts.md"]:
        content = _read_file(persona.memory_dir / filename)
        if content:
            parts.append(f"--- memory/{filename} ---\n{content}")

    # Lessons
    content = _read_file(persona.tasks_dir / "lessons.md")
    if content:
        parts.append(f"--- tasks/lessons.md ---\n{content}")

    # Current date/time
    today = config.cairo_today()
    now = config.cairo_now()
    parts.append(
        f"--- Current Date/Time ---\n"
        f"Today is {today.isoformat()} ({now.strftime('%A')}). "
        f"Current time: {now.strftime('%H:%M')} ({persona.timezone})."
    )

    # Recent daily logs
    for days_ago in range(3):
        day = today - timedelta(days=days_ago)
        daily_file = persona.memory_dir / "daily" / f"{day.isoformat()}.md"
        content = _read_file(daily_file)
        if content:
            max_chars = 4000 if days_ago == 0 else 2000
            if len(content) > max_chars:
                content = content[:max_chars] + "\n...(truncated)"
            parts.append(f"--- memory/daily/{day.isoformat()}.md ---\n{content}")

    context = "\n\n".join(parts)
    logger.debug(f"Soul context for {persona.key}: {len(context)} chars")
    return context


def load_memory_refresh(persona: PersonaConfig, message_text: str = "") -> str:
    """Minimal context for resumed sessions — soul is already in history."""
    parts = ["[Memory refresh — session already has full soul context]"]

    for filename in ["MEMORY.md", "projects.md"]:
        content = _read_file(persona.memory_dir / filename)
        if content:
            parts.append(f"--- memory/{filename} ---\n{content}")

    today = config.cairo_today()
    now = config.cairo_now()
    parts.append(
        f"--- Current Date/Time ---\n"
        f"Today is {today.isoformat()} ({now.strftime('%A')}). "
        f"Current time: {now.strftime('%H:%M')} ({persona.timezone})."
    )

    daily_file = persona.memory_dir / "daily" / f"{today.isoformat()}.md"
    content = _read_file(daily_file)
    if content:
        if len(content) > 3000:
            content = content[-3000:]
        parts.append(f"--- memory/daily/{today.isoformat()}.md (recent) ---\n{content}")

    return "\n\n".join(parts)
