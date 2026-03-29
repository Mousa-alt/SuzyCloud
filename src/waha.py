"""OpenSuzy — Waha WhatsApp API client.

Async httpx client ported from WhatsApp Task Manager's whatsapp.py patterns.
All functions return None on failure (never raise).
"""

import asyncio
import logging
import threading
from typing import Optional

import httpx

from src import config

logger = logging.getLogger(__name__)

# Shared async client (created lazily, thread-safe)
_client: Optional[httpx.AsyncClient] = None
_client_lock = threading.Lock()


def _get_client() -> httpx.AsyncClient:
    """Lazy-init the httpx async client (thread-safe, double-checked locking)."""
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:
            return _client

        _client = httpx.AsyncClient(
            base_url=config.WAHA_API_URL.rstrip("/"),
            headers={
                "Content-Type": "application/json",
                "X-Api-Key": config.WAHA_API_KEY,
            },
            timeout=10.0,
        )
    return _client


async def _request(method: str, endpoint: str, payload: dict = None,
                   timeout: float = 10.0, label: str = "") -> Optional[dict]:
    """Send HTTP request to Waha. Returns response JSON or None."""
    if not config.WAHA_API_URL:
        logger.error("WAHA_API_URL not configured")
        return None

    client = _get_client()
    try:
        response = await client.request(
            method, endpoint, json=payload, timeout=timeout
        )
        if response.status_code in (200, 201):
            try:
                return response.json()
            except Exception:
                return {"ok": True}

        logger.error(
            f"Waha {label or endpoint} failed: "
            f"{response.status_code} {response.text[:200]}"
        )
        return None

    except httpx.TimeoutException:
        logger.error(f"Waha {label or endpoint} timeout")
        return None
    except Exception as e:
        logger.error(f"Waha {label or endpoint} error: {e}")
        return None


# =========================================================================
# Send functions
# =========================================================================

async def send_message(chat_id: str, text: str) -> bool:
    """Send a text message."""
    result = await _request("POST", "/api/sendText", {
        "chatId": chat_id,
        "text": text,
        "session": config.WAHA_SESSION,
    }, label="sendText")
    if result is not None:
        try:
            from src import delivery_tracker
            asyncio.create_task(delivery_tracker.track_outbound(
                chat_id, text, _extract_msg_id(result)))
        except Exception as e:
            logger.warning(f"Delivery tracking failed for sendText: {e}")
    return result is not None


async def send_reply(chat_id: str, text: str, reply_to: str) -> bool:
    """Send a reply to a specific message (threaded)."""
    result = await _request("POST", "/api/sendText", {
        "chatId": chat_id,
        "text": text,
        "reply_to": reply_to,
        "session": config.WAHA_SESSION,
    }, label="sendReply")
    if result is not None:
        try:
            from src import delivery_tracker
            asyncio.create_task(delivery_tracker.track_outbound(
                chat_id, text, _extract_msg_id(result)))
        except Exception as e:
            logger.warning(f"Delivery tracking failed for sendReply: {e}")
    return result is not None


async def send_reaction(message_id: str, emoji: str,
                        sender: str = "") -> bool:
    """React to a message. Empty string removes reaction.

    Tries Waha first, falls back to Baileys gateway if Waha fails.
    sender is the participant JID (needed for group reactions via Baileys).
    """
    result = await _request("PUT", "/api/reaction", {
        "messageId": message_id,
        "reaction": emoji,
        "session": config.WAHA_SESSION,
    }, label="reaction")
    if result is not None:
        return True

    # Fallback: send via Baileys gateway
    return await _gateway_reaction(message_id, emoji, sender=sender)


async def _gateway_reaction(message_id: str, emoji: str,
                            sender: str = "") -> bool:
    """Send a reaction via the Baileys gateway."""
    if not config.GATEWAY_ENABLED:
        return False

    # Parse serialized Waha message ID: "fromMe_chatId_innerMessageId"
    parts = message_id.split("_", 2)
    if len(parts) < 3:
        logger.warning(f"Cannot parse message ID for gateway reaction: {message_id}")
        return False

    from_me = parts[0].lower() == "true"
    chat_id = parts[1]
    inner_id = parts[2]

    gw_url = config.GATEWAY_URL.rstrip("/")
    headers = {}
    if config.GATEWAY_API_KEY:
        headers["X-Api-Key"] = config.GATEWAY_API_KEY

    payload = {
        "chatId": chat_id,
        "messageId": inner_id,
        "reaction": emoji,
        "fromMe": from_me,
    }
    # Group messages need participant JID for Baileys reactions
    if (sender and chat_id.endswith("@g.us")
            and sender != chat_id and not sender.endswith("@g.us")):
        payload["participant"] = sender

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{gw_url}/send-reaction",
                json=payload,
                headers=headers,
            )
            if resp.status_code == 200:
                logger.info(f"Gateway reaction: {emoji} on {message_id[:30]}...")
                return True
            logger.warning(f"Gateway reaction failed: {resp.status_code} {resp.text[:200]}")
            return False
    except httpx.ConnectError:
        logger.warning("Baileys gateway not running for reaction fallback")
        return False
    except Exception as e:
        logger.error(f"Gateway reaction error: {e}")
        return False


async def send_typing(chat_id: str) -> bool:
    """Show typing indicator."""
    result = await _request("POST", "/api/startTyping", {
        "chatId": chat_id,
        "session": config.WAHA_SESSION,
    }, label="startTyping")
    return result is not None


async def send_seen(chat_id: str) -> bool:
    """Mark all messages in chat as read."""
    result = await _request("POST", "/api/sendSeen", {
        "chatId": chat_id,
        "session": config.WAHA_SESSION,
    }, label="sendSeen")
    return result is not None


async def stop_typing(chat_id: str) -> bool:
    """Stop typing indicator."""
    result = await _request("POST", "/api/stopTyping", {
        "chatId": chat_id,
        "session": config.WAHA_SESSION,
    }, label="stopTyping")
    return result is not None


# =========================================================================
# Baileys media gateway — FALLBACK sender for files/images/voice
# =========================================================================

async def _gateway_send(endpoint: str, chat_id: str,
                        file_path: str, caption: str = "") -> bool:
    """Send media via the Baileys gateway service (single attempt)."""
    if not config.GATEWAY_ENABLED:
        return False

    # Resolve to absolute path — gateway CWD differs from backend CWD
    from pathlib import Path
    abs_path = str(Path(file_path).resolve())

    gw_url = config.GATEWAY_URL.rstrip("/")
    headers = {}
    if config.GATEWAY_API_KEY:
        headers["X-Api-Key"] = config.GATEWAY_API_KEY

    try:
        # 120s timeout for large files (16MB+ PDFs need time to upload to WhatsApp)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{gw_url}{endpoint}",
                json={
                    "chatId": chat_id,
                    "filePath": abs_path,
                    "caption": caption,
                },
                headers=headers,
            )
            if resp.status_code == 200:
                logger.info(f"Gateway {endpoint}: sent {file_path}")
                return True
            logger.warning(
                f"Gateway {endpoint} failed: {resp.status_code} {resp.text[:200]}"
            )
            return False
    except httpx.ConnectError:
        logger.warning("Baileys gateway not running (connection refused)")
        return False
    except Exception as e:
        logger.error(f"Gateway {endpoint} error: {e}")
        return False


async def _gateway_send_with_retry(endpoint: str, chat_id: str,
                                   file_path: str, caption: str = "") -> bool:
    """Send media via gateway with one retry on failure.

    The gateway reconnects every few seconds (conflict cycle with Waha).
    If the first attempt hits a 503 (disconnected), wait 5s and retry —
    this usually catches the next connected window.
    """
    if await _gateway_send(endpoint, chat_id, file_path, caption):
        return True

    # Retry once after waiting for the gateway to reconnect
    logger.info(f"Gateway {endpoint} failed, retrying in 5s...")
    await asyncio.sleep(5)
    return await _gateway_send(endpoint, chat_id, file_path, caption)


# =========================================================================
# Send files / images — Baileys gateway (direct, with retry)
# =========================================================================

async def send_image(chat_id: str, file_path: str,
                     caption: str = "") -> bool:
    """Send an image via Baileys gateway (with retry)."""
    from pathlib import Path

    path = Path(file_path)
    if not path.exists():
        logger.error(f"Image file not found: {file_path}")
        return False

    return await _gateway_send_with_retry("/send-image", chat_id, file_path, caption)


async def send_file(chat_id: str, file_path: str,
                    caption: str = "") -> bool:
    """Send a document/file via Baileys gateway (with retry)."""
    from pathlib import Path

    path = Path(file_path)
    if not path.exists():
        logger.error(f"File not found: {file_path}")
        return False

    return await _gateway_send_with_retry("/send-file", chat_id, file_path, caption)


async def send_voice(chat_id: str, file_path: str) -> bool:
    """Send a voice note via Baileys gateway (ptt audio)."""
    from pathlib import Path

    path = Path(file_path)
    if not path.exists():
        logger.error(f"Voice file not found: {file_path}")
        return False

    return await _gateway_send_with_retry("/send-voice", chat_id, file_path)


# =========================================================================
# Media download (for voice notes and incoming files)
# =========================================================================

async def download_media(url: str) -> Optional[bytes]:
    """Download media bytes from Waha.

    The URL may be absolute or relative to Waha API.
    """
    client = _get_client()
    try:
        # If relative URL, it'll use the base_url from the client
        if url.startswith("http"):
            # Absolute URL — need to replace localhost references
            url = url.replace("localhost:3000", config.WAHA_API_URL.split("//")[1])
            async with httpx.AsyncClient(timeout=30.0) as temp_client:
                response = await temp_client.get(
                    url, headers={"X-Api-Key": config.WAHA_API_KEY}
                )
        else:
            response = await client.get(url, timeout=30.0)

        if response.status_code == 200:
            return response.content

        logger.error(f"Media download failed: {response.status_code}")
        return None

    except Exception as e:
        logger.error(f"Media download error: {e}")
        return None


# =========================================================================
# Health check
# =========================================================================

async def is_healthy() -> bool:
    """Check if Waha session is connected and can deliver messages.

    Performs two levels of verification:
    1. Session status == WORKING
    2. Session has 'me' info (proves actual WhatsApp Web connection,
       catches zombie sessions that report WORKING but can't deliver)
    """
    result = await _request(
        "GET", f"/api/sessions/{config.WAHA_SESSION}",
        label="health"
    )
    if not result:
        return False

    if result.get("status") != "WORKING":
        logger.warning(f"Waha session not WORKING: {result.get('status')}")
        return False

    # Deep check: 'me' proves actual WhatsApp Web connection
    me = result.get("me")
    if not me or not me.get("id"):
        logger.warning(
            "Waha session WORKING but no 'me' info — stale connection"
        )
        return False

    return True


async def get_session_info() -> Optional[dict]:
    """Get full session info for diagnostics."""
    return await _request(
        "GET", f"/api/sessions/{config.WAHA_SESSION}",
        label="session_info"
    )


# =========================================================================
# Chat messages (for delivery canary verification)
# =========================================================================

async def get_chat_messages(chat_id: str, limit: int = 5) -> list[dict]:
    """Get recent messages from a chat. Used by delivery_tracker canary probe."""
    result = await _request(
        "GET",
        f"/api/{config.WAHA_SESSION}/chats/{chat_id}/messages?limit={limit}&downloadMedia=false",
        label="getChatMessages",
    )
    if isinstance(result, list):
        return result
    return []


def _extract_msg_id(result: dict) -> str:
    """Parse Waha send response for the message ID."""
    mid = result.get("id", "")
    if isinstance(mid, dict):
        return mid.get("id", "") or mid.get("_serialized", "")
    return str(mid)


# =========================================================================
# Payload parsing (from WhatsApp Task Manager)
# =========================================================================

def parse_webhook_payload(payload: dict) -> dict:
    """Normalize a Waha webhook payload into a clean structure.

    Returns dict with: event, message_id, group_id, sender, sender_name,
    text, from_me, timestamp, quoted_msg, media_info
    """
    _data = payload.get("_data", {}) or {}

    event = payload.get("event", "")

    # Message ID
    id_obj = payload.get("id")
    if isinstance(id_obj, dict):
        message_id = id_obj.get("id", "")
    else:
        _data_id = _data.get("id")
        if isinstance(_data_id, dict):
            message_id = _data_id.get("id", "")
        else:
            message_id = str(id_obj) if id_obj else ""

    # Group/chat ID
    group_id = (
        payload.get("from", "")
        or payload.get("chatId", "")
        or _data.get("from", "")
    )

    # Sender
    sender = (
        payload.get("participant", "")
        or _data.get("participant", "")
        or payload.get("from", "")
    )

    # Sender name
    sender_name = (
        _data.get("notifyName", "")
        or payload.get("notifyName", "")
        or sender
    )

    # Message text
    text = (
        payload.get("body", "")
        or payload.get("text", "")
        or _data.get("body", "")
        or ""
    )

    # From self — CRITICAL for infinite loop prevention
    from_me = payload.get("fromMe", False)
    if not from_me and isinstance(_data.get("id"), dict):
        from_me = _data["id"].get("fromMe", False)

    # Timestamp
    timestamp = payload.get("timestamp", 0) or _data.get("t", 0)

    # Quoted message
    quoted_msg = (
        _data.get("quotedMsg")
        or payload.get("quotedMsg")
        or _data.get("quotedMsgObj")
        or payload.get("quotedMsgObj")
    )

    # Media info
    media_info = None
    has_media = payload.get("hasMedia", False) or _data.get("isMedia", False)
    if has_media:
        media_type = payload.get("type") or _data.get("type") or "unknown"
        _media = payload.get("media") or {}
        media_info = {
            "media_type": media_type,
            "mimetype": (payload.get("mimetype")
                         or _data.get("mimetype")
                         or _media.get("mimetype")
                         or ""),
            "caption": payload.get("caption", ""),
            "filename": next(
                (str(v) for v in (
                    payload.get("filename"),
                    _media.get("filename"),
                    _data.get("filename"),
                    _data.get("fileName"),
                    payload.get("fileName"),
                ) if isinstance(v, str) and v),
                "",
            ),
        }
        if payload.get("caption"):
            text = text or payload["caption"]

    return {
        "event": event,
        "message_id": message_id,
        "group_id": group_id,
        "sender": sender,
        "sender_name": sender_name,
        "text": text,
        "from_me": from_me,
        "timestamp": timestamp,
        "quoted_msg": quoted_msg,
        "media_info": media_info,
    }


def get_full_message_id(payload_raw: dict) -> str:
    """Extract the full serialized message ID needed for reactions.

    Waha needs the compound ID (e.g., 'false_120363xxx@g.us_AAAA').
    """
    id_obj = payload_raw.get("id")
    if isinstance(id_obj, dict):
        serialized = id_obj.get("_serialized", "")
        if serialized:
            return serialized
        from_me = str(id_obj.get("fromMe", False)).lower()
        remote = id_obj.get("remote", "")
        inner_id = id_obj.get("id", "")
        if remote and inner_id:
            return f"{from_me}_{remote}_{inner_id}"
    return str(id_obj) if id_obj else ""
