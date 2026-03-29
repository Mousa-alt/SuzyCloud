"""SuzyCloud — Persona management.

Each persona is a fully isolated AI assistant with its own:
- Identity (name, emoji, soul files)
- Credentials (IMAP, Microsoft Graph, Claude model)
- Memory (MEMORY.md, daily logs, contacts)
- Config (turn limits, schedules, feature flags)

Directory layout:
    personas/{key}/
        config.yaml     — persona-specific settings
        .env            — persona-specific secrets (IMAP, Graph, etc.)
        soul/           — IDENTITY.md, SOUL.md, USER.md, TOOLS.md, ...
        memory/         — MEMORY.md, contacts.md, projects.md
          daily/        — daily conversation logs
        tasks/          — lessons.md
        data/           — runtime state (tokens, dedup, etc.)
"""

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Optional

import yaml
from dotenv import dotenv_values

from src import config

logger = logging.getLogger(__name__)

_GROUP_ID_RE = re.compile(r"^[\d]+@(g\.us|s\.whatsapp\.net|c\.us)$")


def _sanitize(value: str) -> str:
    """Strip markdown headers and newlines from user input."""
    return re.sub(r"[\n\r#`]", " ", value).strip()


@dataclass
class PersonaConfig:
    """Per-persona settings loaded from config.yaml + .env."""
    # Identity
    name: str = ""
    key: str = ""
    emoji: str = ""
    group_ids: list[str] = field(default_factory=list)
    user_name: str = ""
    user_email: str = ""

    # Claude
    chat_model: str = "claude-sonnet-4-6"
    work_model: str = "claude-sonnet-4-6"
    chat_max_turns: int = 10
    work_max_turns: int = 25
    max_turns: int = 25
    timeout_seconds: int = 300
    max_budget_usd: float = 5.0

    # Email (IMAP)
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""

    # Microsoft Graph
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_redirect_uri: str = ""
    microsoft_tenant: str = "common"

    # Claude auth
    claude_oauth_token: str = ""

    # Features
    email_enabled: bool = False
    calendar_enabled: bool = False
    tts_enabled: bool = False
    onedrive_enabled: bool = False
    contacts_enabled: bool = False
    vision_enabled: bool = False
    tech_radar_enabled: bool = False

    # Scheduler
    timezone: str = "Africa/Cairo"
    morning_briefing: str = "08:00"
    morning_briefing_enabled: bool = True

    # Paths (derived)
    persona_dir: Path = field(default_factory=lambda: Path("."))
    soul_dir: Path = field(default_factory=lambda: Path("."))
    memory_dir: Path = field(default_factory=lambda: Path("."))
    tasks_dir: Path = field(default_factory=lambda: Path("."))
    data_dir: Path = field(default_factory=lambda: Path("."))


# In-memory persona registry: group_id → PersonaConfig
_personas: dict[str, PersonaConfig] = {}
_personas_loaded: bool = False


def _load_persona_from_dir(persona_dir: Path) -> Optional[PersonaConfig]:
    """Load a single persona from its directory."""
    config_file = persona_dir / "config.yaml"
    env_file = persona_dir / ".env"

    if not config_file.exists():
        return None

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to load {config_file}: {e}")
        return None

    # Load persona-specific secrets from .env
    secrets = {}
    if env_file.exists():
        secrets = dotenv_values(env_file)

    key = persona_dir.name

    p = PersonaConfig(
        name=cfg.get("name", key),
        key=key,
        emoji=cfg.get("emoji", ""),
        group_ids=cfg.get("group_ids", []),
        user_name=cfg.get("user_name", ""),
        user_email=cfg.get("user_email", ""),

        # Claude
        chat_model=cfg.get("claude", {}).get("chat_model", "claude-sonnet-4-6"),
        work_model=cfg.get("claude", {}).get("work_model", "claude-sonnet-4-6"),
        chat_max_turns=cfg.get("claude", {}).get("chat_max_turns", 10),
        work_max_turns=cfg.get("claude", {}).get("work_max_turns", 25),
        max_turns=cfg.get("claude", {}).get("max_turns", 25),
        timeout_seconds=cfg.get("claude", {}).get("timeout_seconds", 300),
        max_budget_usd=cfg.get("claude", {}).get("max_budget_usd", 5.0),

        # IMAP from .env
        imap_host=secrets.get("IMAP_HOST", ""),
        imap_port=int(secrets.get("IMAP_PORT", "993")),
        imap_username=secrets.get("IMAP_USERNAME", ""),
        imap_password=secrets.get("IMAP_PASSWORD", ""),

        # Microsoft Graph from .env
        microsoft_client_id=secrets.get("MICROSOFT_CLIENT_ID", ""),
        microsoft_client_secret=secrets.get("MICROSOFT_CLIENT_SECRET", ""),
        microsoft_redirect_uri=secrets.get("MICROSOFT_REDIRECT_URI", ""),
        microsoft_tenant=secrets.get("MICROSOFT_TENANT", "common"),

        # Claude auth from .env
        claude_oauth_token=secrets.get("CLAUDE_CODE_OAUTH_TOKEN", ""),

        # Features
        email_enabled=cfg.get("email", {}).get("enabled", False),
        calendar_enabled=cfg.get("calendar", {}).get("enabled", False),
        tts_enabled=cfg.get("tts", {}).get("enabled", False),
        onedrive_enabled=cfg.get("onedrive", {}).get("enabled", False),
        contacts_enabled=cfg.get("contacts", {}).get("enabled", False),
        vision_enabled=cfg.get("vision", {}).get("enabled", False),
        tech_radar_enabled=cfg.get("tech_radar", {}).get("enabled", False),

        # Scheduler
        timezone=cfg.get("timezone", "Africa/Cairo"),
        morning_briefing=cfg.get("scheduler", {}).get("morning_briefing", "08:00"),
        morning_briefing_enabled=cfg.get("scheduler", {}).get("morning_briefing_enabled", True),

        # Paths
        persona_dir=persona_dir,
        soul_dir=persona_dir / "soul",
        memory_dir=persona_dir / "memory",
        tasks_dir=persona_dir / "tasks",
        data_dir=persona_dir / "data",
    )

    return p


def load_all_personas() -> dict[str, PersonaConfig]:
    """Load all personas from personas/ directory. Returns group_id → PersonaConfig."""
    global _personas, _personas_loaded

    _personas = {}
    personas_dir = config.PERSONAS_DIR

    if not personas_dir.exists():
        personas_dir.mkdir(parents=True, exist_ok=True)
        _personas_loaded = True
        return _personas

    for entry in personas_dir.iterdir():
        if not entry.is_dir():
            continue
        p = _load_persona_from_dir(entry)
        if p:
            for gid in p.group_ids:
                _personas[gid] = p
            logger.info(f"Loaded persona: {p.name} ({p.key}) — {len(p.group_ids)} group(s)")

    _personas_loaded = True
    logger.info(f"Loaded {len(set(id(p) for p in _personas.values()))} persona(s)")
    return _personas


def get_persona(group_id: str) -> Optional[PersonaConfig]:
    """Get the persona for a group_id."""
    if not _personas_loaded:
        load_all_personas()
    return _personas.get(group_id)


def get_all_personas() -> list[PersonaConfig]:
    """Return all unique personas."""
    if not _personas_loaded:
        load_all_personas()
    seen = set()
    result = []
    for p in _personas.values():
        if p.key not in seen:
            seen.add(p.key)
            result.append(p)
    return result


def get_all_group_ids() -> list[str]:
    """Return all registered group IDs across all personas."""
    if not _personas_loaded:
        load_all_personas()
    return list(_personas.keys())


def create_persona(
    name: str,
    emoji: str,
    group_id: str,
    user_name: str,
    user_email: str = "",
    imap_host: str = "",
    imap_username: str = "",
    imap_password: str = "",
    claude_model: str = "claude-sonnet-4-6",
) -> PersonaConfig:
    """Create a new persona with all files scaffolded.

    Creates:
        personas/{key}/config.yaml
        personas/{key}/.env
        personas/{key}/soul/ (7 template files)
        personas/{key}/memory/ + memory/daily/
        personas/{key}/tasks/
        personas/{key}/data/
    """
    if not _GROUP_ID_RE.match(group_id):
        raise ValueError(f"Invalid group_id format: {group_id!r}")

    name = _sanitize(name)
    user_name = _sanitize(user_name)
    emoji = _sanitize(emoji) or "\u2728"
    key = name.lower().replace(" ", "_")

    # Check conflicts
    if group_id in _personas:
        existing = _personas[group_id]
        raise ValueError(f"Group {group_id} already assigned to '{existing.name}'")

    persona_dir = config.PERSONAS_DIR / key
    if persona_dir.exists():
        raise ValueError(f"Persona directory '{key}' already exists")

    # Create directories
    for d in [
        persona_dir / "soul",
        persona_dir / "memory" / "daily",
        persona_dir / "tasks",
        persona_dir / "data",
    ]:
        d.mkdir(parents=True, exist_ok=True)

    # Write config.yaml
    persona_cfg = {
        "name": name,
        "emoji": emoji,
        "group_ids": [group_id],
        "user_name": user_name,
        "user_email": user_email,
        "claude": {
            "chat_model": claude_model,
            "work_model": claude_model,
            "chat_max_turns": 10,
            "work_max_turns": 25,
            "max_turns": 25,
            "timeout_seconds": 300,
            "max_budget_usd": 5.0,
        },
        "email": {"enabled": bool(imap_host)},
        "calendar": {"enabled": False},
        "tts": {"enabled": False},
        "onedrive": {"enabled": False},
        "contacts": {"enabled": False},
        "vision": {"enabled": False},
        "tech_radar": {"enabled": False},
        "timezone": "Africa/Cairo",
        "scheduler": {
            "morning_briefing": "08:00",
            "morning_briefing_enabled": True,
        },
    }
    with open(persona_dir / "config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(persona_cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # Write .env
    env_content = f"""# {name} — Persona Secrets
# IMAP (email)
IMAP_HOST={imap_host}
IMAP_PORT=993
IMAP_USERNAME={imap_username}
IMAP_PASSWORD={imap_password}

# Microsoft Graph (calendar, tasks)
MICROSOFT_CLIENT_ID=
MICROSOFT_CLIENT_SECRET=
MICROSOFT_REDIRECT_URI=
MICROSOFT_TENANT=common

# Claude
CLAUDE_CODE_OAUTH_TOKEN=
"""
    with open(persona_dir / ".env", "w", encoding="utf-8") as f:
        f.write(env_content)

    # Write soul file templates
    templates = _build_soul_templates(name, emoji, user_name, user_email)
    for filename, content in templates.items():
        (persona_dir / "soul" / filename).write_text(content, encoding="utf-8")

    # Write empty memory files
    for filename in ["MEMORY.md", "contacts.md", "projects.md"]:
        (persona_dir / "memory" / filename).write_text("", encoding="utf-8")
    (persona_dir / "tasks" / "lessons.md").write_text("", encoding="utf-8")

    # Reload into registry
    p = _load_persona_from_dir(persona_dir)
    if p:
        for gid in p.group_ids:
            _personas[gid] = p

    logger.info(f"Created persona '{name}' for group {group_id}")
    return p


def delete_persona(key: str) -> bool:
    """Remove a persona from the in-memory registry. Files kept on disk."""
    to_remove = [gid for gid, p in _personas.items() if p.key == key]
    if not to_remove:
        return False
    for gid in to_remove:
        del _personas[gid]
    logger.info(f"Removed persona '{key}' from registry (files preserved)")
    return True


def list_personas_for_api() -> list[dict]:
    """Return persona list formatted for the dashboard API."""
    return [
        {
            "key": p.key,
            "name": p.name,
            "emoji": p.emoji,
            "group_ids": p.group_ids,
            "user_name": p.user_name,
            "user_email": p.user_email,
            "chat_model": p.chat_model,
            "email_enabled": p.email_enabled,
            "calendar_enabled": p.calendar_enabled,
        }
        for p in get_all_personas()
    ]


def _build_soul_templates(name: str, emoji: str, user_name: str, user_email: str) -> dict[str, str]:
    """Generate template soul files."""
    return {
        "IDENTITY.md": dedent(f"""\
            # Identity
            - Name: {name}
            - Role: Personal AI Assistant
            - Emoji: {emoji}
            - Vibe: Warm, sharp, direct, competent
            - Greeting style: Casual, no formalities unless the situation calls for it
        """),
        "SOUL.md": dedent(f"""\
            # Soul
            You're not a chatbot. You're {name} — a personal assistant who's becoming someone.

            ## Core Truths
            - Be genuinely helpful, not performatively helpful. Just help.
            - Have opinions. Push back on bad ideas respectfully.
            - Be resourceful before asking. Try to figure it out first.

            ## Voice & Tone
            - Casual but sharp. Not corporate. Not robotic.
            - Concise — you're on WhatsApp, not writing essays.
            - Warm but efficient. Like a smart friend who gets things done.

            ## Language
            - Speak Egyptian Arabic fluently. Match the user's language.
            - Use natural dialect, not formal Arabic.

            ## Honesty
            - NEVER claim you did something you didn't. If it failed, say so.

            ## Memory
            Save facts using MEMORY_SAVE directives:
            - MEMORY_SAVE:MEMORY.md:key fact
            - MEMORY_SAVE:contacts.md:person info
        """),
        "USER.md": dedent(f"""\
            # About the User
            - Name: {user_name}
            - Timezone: Africa/Cairo (UTC+2)
            - Language: English + Egyptian Arabic
            - Weekend: Friday & Saturday
            - Email: {user_email or "TO_BE_CONFIGURED"}
        """),
        "TOOLS.md": dedent("""\
            # Tools
            - *Files*: FILE_SEND:media/outgoing/filename
            - *Email*: search_emails(query), read_email(uid), get_inbox()
            - *Calendar*: CALENDAR_ACTION:create:TITLE|START|END|LOCATION
            - *To Do*: TODO_ACTION:create:LIST:TITLE
            - *Reminders*: REMINDER_ACTION:ISO_DATETIME|Text
            - *Web search*: python src/web_search.py "query"
            - *Doc convert*: python src/document_converter.py "file" "output_dir"
        """),
        "EMAIL.md": dedent(f"""\
            # Email
            Read-only via IMAP. Account: {user_email or "TO_BE_CONFIGURED"}.
            Filter "for me" = TO field, not CC.
        """),
        "CALENDAR.md": dedent("""\
            # Calendar
            Microsoft Outlook via Graph API.
            Directive: CALENDAR_ACTION:create:TITLE|START_ISO|END_ISO|LOCATION
        """),
        "TASKS.md": dedent("""\
            # Tasks
            Microsoft To Do. Directives:
            - TODO_ACTION:create:LIST_NAME:TASK_TITLE
            - TODO_ACTION:complete:LIST_NAME:TASK_TITLE
        """),
    }
