"""SuzyCloud -- Response post-processing pipeline.

Processes Claude's response text before sending it back to the user via
WhatsApp.  Handles directive extraction (FILE_SEND, TODO_ACTION,
CALENDAR_ACTION, REMINDER_ACTION, EMAIL_DRAFT, MEMORY_SAVE), message
chunking, and the actual send logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from src import config
from src.persona import PersonaConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Directive regex patterns
# ---------------------------------------------------------------------------

_DIRECTIVE_RE = re.compile(
    r"^[\s*`_~>]*"
    r"(FILE_SEND|TODO_ACTION|CALENDAR_ACTION|REMINDER_ACTION|EMAIL_DRAFT|MEMORY_SAVE)"
    r":(.+)$"
)


def _clean_line(line: str) -> str:
    """Strip markdown formatting wrappers from a line for directive matching."""
    s = line.strip()
    s = re.sub(r"^[`*_~]+", "", s)
    s = re.sub(r"[`*_~]+$", "", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Directive extraction
# ---------------------------------------------------------------------------

def _extract_directives(text: str) -> tuple[str, list[dict]]:
    """Parse all directives out of response text.

    Returns (cleaned_text, list_of_directive_dicts).
    Each directive dict has keys: type, raw (the part after TYPE:).
    """
    clean_lines: list[str] = []
    directives: list[dict] = []

    for line in text.split("\n"):
        cleaned = _clean_line(line)
        match = _DIRECTIVE_RE.match(cleaned)
        if match:
            directives.append({
                "type": match.group(1),
                "raw": match.group(2).strip(),
            })
        else:
            clean_lines.append(line)

    return "\n".join(clean_lines), directives


# ---------------------------------------------------------------------------
# Directive executors
# ---------------------------------------------------------------------------

async def _exec_file_send(
    directive: dict,
    group_id: str,
    waha_client,
    persona: PersonaConfig,
) -> Optional[str]:
    """Execute a FILE_SEND directive. Returns error message or None."""
    parts = directive["raw"].split("|", 1)
    path_str = parts[0].strip()
    caption = parts[1].strip() if len(parts) > 1 else ""

    # Resolve relative to project root, restrict to media/outgoing/
    full_path = (config.PROJECT_ROOT / path_str).resolve()
    allowed_dir = (config.PROJECT_ROOT / "media" / "outgoing").resolve()

    if not full_path.is_relative_to(allowed_dir):
        logger.warning(f"FILE_SEND rejected (outside media/outgoing/): {path_str}")
        return f"File rejected (security): {Path(path_str).name}"

    if not full_path.exists():
        logger.warning(f"FILE_SEND path not found: {full_path}")
        return f"File not found: {Path(path_str).name}"

    logger.info(f"FILE_SEND: sending {path_str}")
    ok = await waha_client.send_file(group_id, str(full_path), caption)
    if not ok:
        return f"Failed to send: {Path(path_str).name}"
    return None


def _exec_todo_action(directive: dict, persona: PersonaConfig) -> None:
    """Log a TODO_ACTION directive.

    # TODO: implement with persona-specific Graph tokens
    """
    parts = directive["raw"].split(":", 2)
    action = parts[0].strip().lower() if parts else ""
    list_name = parts[1].strip() if len(parts) > 1 else ""
    extra = parts[2].strip() if len(parts) > 2 else ""
    logger.info(
        f"TODO_ACTION [{persona.name}]: {action} | list={list_name} | extra={extra} "
        f"(not executed — Graph tokens not yet implemented)"
    )


def _exec_calendar_action(directive: dict, persona: PersonaConfig) -> None:
    """Log a CALENDAR_ACTION directive.

    # TODO: implement with persona-specific Graph tokens
    """
    logger.info(
        f"CALENDAR_ACTION [{persona.name}]: {directive['raw']} "
        f"(not executed — Graph tokens not yet implemented)"
    )


def _exec_reminder_action(
    directive: dict,
    group_id: str,
    persona: PersonaConfig,
) -> None:
    """Save a reminder to persona's data_dir/reminders.json."""
    parts = directive["raw"].split("|", 1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        logger.warning(f"REMINDER_ACTION: malformed directive: {directive['raw']}")
        return

    due_str = parts[0].strip()
    reminder_text = parts[1].strip()

    # Validate ISO datetime
    try:
        datetime.fromisoformat(due_str)
    except ValueError:
        logger.warning(f"REMINDER_ACTION: invalid datetime: {due_str}")
        return

    reminders_path = persona.data_dir / "reminders.json"
    reminders_path.parent.mkdir(parents=True, exist_ok=True)

    existing: list[dict] = []
    if reminders_path.exists():
        try:
            existing = json.loads(reminders_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except (json.JSONDecodeError, OSError):
            logger.exception(
                "REMINDER_ACTION: failed to read reminders.json; "
                "skipping write to avoid data loss"
            )
            return

    existing.append({
        "due": due_str,
        "text": reminder_text,
        "chat_id": group_id,
    })

    try:
        reminders_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"REMINDER_ACTION: scheduled '{reminder_text}' at {due_str}")
    except OSError:
        logger.exception("REMINDER_ACTION: failed to write reminders.json")


def _exec_email_draft(directive: dict, persona: PersonaConfig) -> None:
    """Save an email draft to persona's data_dir/email_drafts.json."""
    parts = directive["raw"].split("|", 2)
    if len(parts) < 3:
        logger.warning(f"EMAIL_DRAFT: malformed (need to|subject|body): {directive['raw']}")
        return

    to_addr = parts[0].strip()
    subject = parts[1].strip()
    body = parts[2].strip().replace("\\n", "\n")

    if not to_addr or not subject:
        logger.warning("EMAIL_DRAFT: empty recipient or subject")
        return

    drafts_path = persona.data_dir / "email_drafts.json"
    drafts_path.parent.mkdir(parents=True, exist_ok=True)

    existing: list[dict] = []
    if drafts_path.exists():
        try:
            existing = json.loads(drafts_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except (json.JSONDecodeError, OSError):
            existing = []

    existing.append({
        "to": to_addr,
        "subject": subject,
        "body": body,
        "created_at": datetime.now().isoformat(),
    })

    try:
        drafts_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"EMAIL_DRAFT: saved draft to {to_addr} re: {subject}")
    except OSError:
        logger.exception("EMAIL_DRAFT: failed to write email_drafts.json")


# ---------------------------------------------------------------------------
# Message chunking
# ---------------------------------------------------------------------------

def split_message(text: str, limit: int = 3500) -> list[str]:
    """Split a long message into WhatsApp-friendly chunks at line boundaries."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line

    if current:
        chunks.append(current)

    return chunks


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def process_and_send(
    response_text: str,
    group_id: str,
    waha_client,
    persona: PersonaConfig,
    first_msg_id: str | None = None,
) -> None:
    """Post-process Claude's response and send it to WhatsApp.

    Steps:
        1. Extract all directives from response text
        2. Clean text (directive lines removed)
        3. Execute directives (FILE_SEND, TODO_ACTION, etc.)
        4. Split long messages into chunks
        5. Send via waha_client
    """
    if not response_text:
        return

    # --- 1. Extract directives ---
    result_text, directives = _extract_directives(response_text)

    # --- 2. Execute directives ---
    file_errors: list[str] = []

    for d in directives:
        dtype = d["type"]
        try:
            if dtype == "FILE_SEND":
                err = await _exec_file_send(d, group_id, waha_client, persona)
                if err:
                    file_errors.append(err)

            elif dtype == "TODO_ACTION":
                _exec_todo_action(d, persona)

            elif dtype == "CALENDAR_ACTION":
                _exec_calendar_action(d, persona)

            elif dtype == "REMINDER_ACTION":
                _exec_reminder_action(d, group_id, persona)

            elif dtype == "EMAIL_DRAFT":
                _exec_email_draft(d, persona)

            elif dtype == "MEMORY_SAVE":
                # Handled upstream in main.py; just strip from output
                logger.debug(f"MEMORY_SAVE directive stripped: {d['raw'][:60]}")

        except Exception:
            logger.exception(f"Error executing {dtype} directive")

    # --- 3. Report file send failures ---
    if file_errors:
        err_msg = "File send issue: " + "; ".join(file_errors)
        await waha_client.send_message(group_id, err_msg)

    # --- 4. Split and send text ---
    result_text = result_text.strip()
    if not result_text:
        return

    chunks = split_message(result_text, 3500)
    for i, chunk in enumerate(chunks):
        await waha_client.send_message(group_id, chunk)
        # Small delay between chunks to preserve ordering
        if i < len(chunks) - 1:
            await asyncio.sleep(0.3)
