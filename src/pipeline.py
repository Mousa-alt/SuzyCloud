"""SuzyCloud — Message preprocessing pipeline.

Enriches raw incoming messages before passing them to Claude.
Handles voice transcription markers, image references, and file references.
"""

import logging
from typing import Optional

from src.persona import PersonaConfig

logger = logging.getLogger(__name__)


async def preprocess_message(
    combined_text: str,
    media_items: list,
    persona: PersonaConfig,
) -> str:
    """Return enriched message text for Claude.

    Processing steps:
    1. Voice transcription: strips the prefix marker, passes text naturally.
    2. Image references: appends a note about attached images.
    3. File references: appends a note about attached files.

    Args:
        combined_text: The raw message text (may include [Voice Transcription]: prefix).
        media_items: List of media dicts with keys: type, mimetype, filename, path.
        persona: The persona config (for future per-persona processing).

    Returns:
        Enriched text string ready for Claude.
    """
    parts: list[str] = []

    # --- Voice transcription ---
    text = combined_text.strip()
    if text.startswith("[Voice Transcription]:"):
        # Strip the marker — Claude should respond naturally to the transcribed content
        text = text[len("[Voice Transcription]:"):].strip()

    if text:
        parts.append(text)

    # --- Media attachments ---
    images: list[str] = []
    files: list[str] = []

    for item in (media_items or []):
        media_type = item.get("type", "")
        mimetype = item.get("mimetype", "")
        filename = item.get("filename", "unknown")
        path = item.get("path", "")

        if media_type == "image" or mimetype.startswith("image/"):
            images.append(f"{filename} ({path})" if path else filename)
        else:
            label = filename
            if mimetype:
                label = f"{filename} [{mimetype}]"
            files.append(f"{label} ({path})" if path else label)

    if images:
        img_list = ", ".join(images)
        if len(images) == 1:
            parts.append(f"[The user sent an image: {img_list}]")
        else:
            parts.append(f"[The user sent {len(images)} images: {img_list}]")

    if files:
        file_list = ", ".join(files)
        if len(files) == 1:
            parts.append(f"[The user sent a file: {file_list}]")
        else:
            parts.append(f"[The user sent {len(files)} files: {file_list}]")

    result = "\n\n".join(parts)

    if not result:
        # Edge case: empty message with no media
        result = "[Empty message received]"

    return result
