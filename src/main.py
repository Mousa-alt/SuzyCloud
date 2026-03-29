"""SuzyCloud — Multi-tenant WhatsApp AI assistant platform.

Single process handles all personas. Each WhatsApp group is mapped to a
persona with its own soul, memory, credentials, and Claude config.

Message flow:
    Waha webhook → /webhook → persona lookup → batch → soul context →
    Claude CLI → response processing → WhatsApp reply
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src import config
from src.persona import (
    PersonaConfig,
    get_persona,
    get_all_group_ids,
    load_all_personas,
    list_personas_for_api,
    create_persona,
    delete_persona,
)
from src.soul_loader import load_soul_context, load_memory_refresh
from src.batcher import MessageBatcher, merge_batched_messages
from src import waha as waha_module

logger = logging.getLogger(__name__)

# Remove CLAUDECODE env var to prevent nested session errors
os.environ.pop("CLAUDECODE", None)

# --- Globals ---
_batcher: MessageBatcher | None = None
_waha: waha_module.WahaClient | None = None

# Rate limiting
_rate_windows: dict[str, list[float]] = defaultdict(list)

# Dedup
_dedup_cache: dict[str, float] = {}
_DEDUP_TTL = 3600


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown."""
    global _batcher, _waha

    config.setup_logging()
    logger.info("SuzyCloud starting...")

    # Load all personas
    load_all_personas()
    group_ids = get_all_group_ids()
    logger.info(f"Loaded {len(group_ids)} group(s) across all personas")

    # Init Waha client
    _waha = waha_module.WahaClient(
        api_url=config.WAHA_API_URL,
        api_key=config.WAHA_API_KEY,
        session=config.WAHA_SESSION,
    )

    # Init batcher
    _batcher = MessageBatcher(on_flush=_on_batch_flush)
    _batcher.set_loop(asyncio.get_event_loop())

    logger.info(f"SuzyCloud ready on port {config.WEBHOOK_PORT}")
    logger.info(f"Allowed groups: {group_ids or 'all'}")

    yield

    # Shutdown
    if _batcher:
        _batcher.flush_all()
    logger.info("SuzyCloud stopped")


app = FastAPI(lifespan=lifespan)


# =========================================================================
# Webhook endpoint
# =========================================================================

def _verify_auth(request: Request) -> bool:
    """Check webhook authentication."""
    secret = config.WEBHOOK_SECRET
    if not secret:
        return True
    api_key = request.headers.get("X-Api-Key", "")
    token = request.query_params.get("token", "")
    return hmac.compare_digest(api_key, secret) or hmac.compare_digest(token, secret)


def _dedup_key(message_id: str, sender: str, text: str) -> str:
    """Generate dedup key."""
    if message_id:
        return message_id
    return hashlib.md5(f"{sender}:{text}:{int(time.time() / 60)}".encode()).hexdigest()


def _check_rate_limit(group_id: str) -> bool:
    """Sliding window rate limiter per group."""
    now = time.time()
    window = _rate_windows[group_id]
    window[:] = [t for t in window if now - t < 60]
    if len(window) >= config.MAX_REQUESTS_PER_MINUTE:
        return False
    window.append(now)
    return True


@app.post("/webhook")
async def webhook(request: Request):
    """Receive WhatsApp messages from Waha."""
    if not _verify_auth(request):
        return JSONResponse(status_code=401, content={"status": "unauthorized"})

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "bad_request"})

    # Parse Waha payload
    data = waha_module.parse_webhook_payload(payload)
    if not data or data.get("from_me"):
        return JSONResponse({"status": "skipped"})

    group_id = data.get("group_id", "")
    text = data.get("text", "")
    sender = data.get("sender", "")
    sender_name = data.get("sender_name", "")
    message_id = data.get("message_id", "")

    # Check if this group belongs to a persona
    allowed = get_all_group_ids()
    if allowed and group_id not in allowed:
        return JSONResponse({"status": "ignored", "reason": "no_persona"})

    # Dedup
    dk = _dedup_key(message_id, sender, text)
    now = time.time()
    if dk in _dedup_cache and now - _dedup_cache[dk] < _DEDUP_TTL:
        return JSONResponse({"status": "duplicate"})
    _dedup_cache[dk] = now

    # Rate limit
    if not _check_rate_limit(group_id):
        return JSONResponse({"status": "rate_limited"})

    # Skip empty
    if not text and not data.get("media_info"):
        return JSONResponse({"status": "empty"})

    # Add to batcher
    if _batcher:
        _batcher.add(
            group_id=group_id,
            sender=sender,
            sender_name=sender_name,
            message={
                "text": text,
                "message_id": message_id,
                "media_info": data.get("media_info"),
                "sender_name": sender_name,
            },
        )

    return JSONResponse({"status": "queued"})


# =========================================================================
# Batch flush — process messages through Claude
# =========================================================================

async def _on_batch_flush(group_id: str, sender: str, sender_name: str, messages: list):
    """Process a batch of messages for a persona."""
    if not messages or not _waha:
        return

    # Look up persona
    persona = get_persona(group_id)
    if not persona:
        logger.warning(f"No persona for group {group_id}")
        return

    merged = merge_batched_messages(messages)
    combined_text = merged["combined_text"]
    if not combined_text:
        return

    logger.info(f"[{persona.name}] Processing: {combined_text[:80]}...")

    # Load soul context
    from src import sessions as sessions_module
    session_id = await sessions_module.get_session_id(group_id)
    is_resumed = session_id is not None

    if is_resumed:
        soul_context = load_memory_refresh(persona, message_text=combined_text)
    else:
        soul_context = load_soul_context(persona, message_text=combined_text)

    # Invoke Claude
    from src import claude_runner
    try:
        result_text, new_session_id = await claude_runner.run(
            message=combined_text,
            session_id=session_id,
            soul_context=soul_context,
            model=persona.chat_model,
            max_turns=persona.chat_max_turns,
            is_resumed=is_resumed,
        )
    except Exception as e:
        logger.error(f"[{persona.name}] Claude error: {e}")
        await _waha.send_message(group_id, f"Sorry, I hit an error: {e}")
        return

    # Save session
    if new_session_id:
        await sessions_module.set_session_id(group_id, new_session_id)

    # Send response
    if result_text:
        # Process MEMORY_SAVE directives (write to persona's memory dir)
        result_text = _execute_memory_saves(result_text, persona)

        # Chunk and send
        chunks = _split_message(result_text)
        for chunk in chunks:
            await _waha.send_message(group_id, chunk)

    # Log to persona's daily file
    _log_exchange(persona, sender_name, combined_text, result_text or "")


# =========================================================================
# Response processing helpers
# =========================================================================

import re as _re


def _execute_memory_saves(text: str, persona: PersonaConfig) -> str:
    """Extract and execute MEMORY_SAVE directives into persona's memory dir."""
    clean_lines = []
    memory_dir = persona.memory_dir
    allowed_files = {"MEMORY.md", "projects.md", "contacts.md", "lessons.md"}

    for line in text.split("\n"):
        cleaned = _re.sub(r"^[\s*`_~>]+|[\s*`_~>]+$", "", line).strip()
        match = _re.match(r"^MEMORY_SAVE:(.+?):(.+)$", cleaned)
        if match:
            filename = match.group(1).strip()
            content = match.group(2).strip()
            is_daily = filename.startswith("daily/") and filename.endswith(".md")
            if filename not in allowed_files and not is_daily:
                continue
            memory_path = (memory_dir / filename).resolve()
            if not memory_path.is_relative_to(memory_dir.resolve()):
                continue
            try:
                memory_path.parent.mkdir(parents=True, exist_ok=True)
                with open(memory_path, "a", encoding="utf-8") as f:
                    f.write(f"\n- {content}")
                logger.info(f"[{persona.name}] MEMORY_SAVE: {filename}")
            except Exception as e:
                logger.error(f"MEMORY_SAVE error: {e}")
            continue
        clean_lines.append(line)

    return "\n".join(clean_lines)


def _split_message(text: str, limit: int = 3500) -> list[str]:
    """Split long messages into WhatsApp-friendly chunks."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def _log_exchange(persona: PersonaConfig, sender_name: str, user_msg: str, assistant_msg: str):
    """Log conversation to persona's daily file."""
    today = config.cairo_today().isoformat()
    now = config.cairo_now().strftime("%H:%M")
    daily_dir = persona.memory_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_file = daily_dir / f"{today}.md"

    user_short = user_msg[:1000] if len(user_msg) > 1000 else user_msg
    asst_short = assistant_msg[:1500] if len(assistant_msg) > 1500 else assistant_msg

    entry = f"\n### {now} — {sender_name}\n**User:** {user_short}\n**{persona.name}:** {asst_short}\n"

    try:
        with open(daily_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        logger.error(f"Failed to log exchange: {e}")


# =========================================================================
# Health + Dashboard API
# =========================================================================

@app.get("/health")
async def health():
    return {"status": "ok", "personas": len(get_all_group_ids())}


@app.get("/api/personas")
async def api_list_personas():
    return list_personas_for_api()


@app.post("/api/personas")
async def api_create_persona(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    emoji = (body.get("emoji") or "").strip() or "\u2728"
    group_id = (body.get("group_id") or "").strip()
    user_name = (body.get("user_name") or "").strip()
    user_email = (body.get("user_email") or "").strip()
    imap_host = (body.get("imap_host") or "").strip()
    imap_username = (body.get("imap_username") or "").strip()
    imap_password = (body.get("imap_password") or "").strip()
    claude_model = (body.get("claude_model") or "claude-sonnet-4-6").strip()

    if not name or not group_id or not user_name:
        return JSONResponse(status_code=400, content={"error": "name, group_id, user_name required"})

    try:
        p = create_persona(
            name=name, emoji=emoji, group_id=group_id,
            user_name=user_name, user_email=user_email,
            imap_host=imap_host, imap_username=imap_username,
            imap_password=imap_password, claude_model=claude_model,
        )
        return {"key": p.key, "name": p.name, "status": "created"}
    except ValueError as e:
        return JSONResponse(status_code=409, content={"error": str(e)})


@app.delete("/api/personas/{key}")
async def api_delete_persona(key: str):
    if delete_persona(key):
        return {"status": "deleted"}
    return JSONResponse(status_code=404, content={"error": "not found"})
