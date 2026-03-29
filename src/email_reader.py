"""SuzyCloud — Per-persona IMAP email reader (read-only).

Each persona has its own IMAP credentials in PersonaConfig.
Connections are short-lived (connect/disconnect per call) for simplicity.
"""

import email
import email.header
import email.message
import email.utils
import imaplib
import logging
import os
import re
from datetime import timezone
from typing import Optional

from src.persona import PersonaConfig

# Fix: SSLKEYLOGFILE pointing to inaccessible virtual file breaks SSL on Windows
if os.environ.get("SSLKEYLOGFILE", "").startswith("\\\\?\\Volume"):
    del os.environ["SSLKEYLOGFILE"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_configured(persona: PersonaConfig) -> Optional[dict]:
    """Return error dict if IMAP is not configured for this persona."""
    if not persona.imap_host or not persona.imap_username or not persona.imap_password:
        return {"error": "Email not configured for this persona"}
    return None


def _connect(persona: PersonaConfig) -> tuple[Optional[imaplib.IMAP4_SSL], Optional[dict]]:
    """Open a fresh IMAP connection using the persona's credentials."""
    err = _check_configured(persona)
    if err:
        return None, err

    try:
        conn = imaplib.IMAP4_SSL(persona.imap_host, persona.imap_port)
        conn.login(persona.imap_username, persona.imap_password)
        return conn, None
    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP login failed for {persona.key}: {e}")
        return None, {"error": "auth_failed", "message": "Email login failed. Check IMAP credentials."}
    except Exception as e:
        logger.error(f"IMAP connection error for {persona.key}: {e}")
        return None, {"error": "connection_failed", "message": f"Cannot connect to mail server: {persona.imap_host}"}


def _disconnect(conn: Optional[imaplib.IMAP4_SSL]) -> None:
    """Safely close an IMAP connection."""
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass
    try:
        conn.logout()
    except Exception:
        pass


def _decode_header(raw: str) -> str:
    """Decode an email header value (handles encoded-word syntax)."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


def _parse_date(date_str: str) -> str:
    """Parse email date to ISO format."""
    if not date_str:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
        return parsed.astimezone(timezone.utc).isoformat()
    except Exception:
        return date_str


def _get_body_text(msg: email.message.Message) -> str:
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fallback: try HTML and strip tags
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    text = re.sub(r"<[^>]+>", "", html)
                    return re.sub(r"\s+", " ", text).strip()
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                text = re.sub(r"<[^>]+>", "", text)
                text = re.sub(r"\s+", " ", text).strip()
            return text
    return ""


def _parse_addresses(msg: email.message.Message, header: str) -> list[str]:
    """Extract email addresses from a header field (To, Cc, etc.)."""
    addrs = []
    for raw in (msg.get_all(header) or []):
        for _, addr in email.utils.getaddresses([raw]):
            if addr:
                addrs.append(addr)
    return addrs


def _parse_summary(msg_data: bytes, uid: str) -> dict:
    """Parse a fetched email into a summary dict."""
    msg = email.message_from_bytes(msg_data)
    from_raw = msg.get("From", "")
    from_name, from_email_addr = email.utils.parseaddr(from_raw)

    return {
        "uid": uid,
        "subject": _decode_header(msg.get("Subject", "(no subject)")),
        "from_name": _decode_header(from_name) or from_email_addr,
        "from_email": from_email_addr,
        "to": _parse_addresses(msg, "To"),
        "cc": _parse_addresses(msg, "Cc"),
        "date": _parse_date(msg.get("Date", "")),
        "body_preview": _get_body_text(msg)[:200],
        "is_read": False,  # Will be set from FLAGS
        "has_attachments": any(part.get_filename() for part in msg.walk()) if msg.is_multipart() else False,
    }


def _sanitize_imap_value(value: str) -> str:
    """Sanitize a value for use in IMAP search criteria."""
    return value.replace('"', '').replace('\\', '').replace('\r', '').replace('\n', '').replace('\x00', '')


def _build_search_criteria(query: str) -> list[str]:
    """Convert user-friendly query to IMAP search criteria."""
    query = query.strip()

    def _quote(val: str) -> str:
        sanitized = _sanitize_imap_value(val)
        return f'"{sanitized}"'

    if query.lower().startswith("from:"):
        return ["FROM", _quote(query[5:].strip())]
    if query.lower().startswith("subject:"):
        return ["SUBJECT", _quote(query[8:].strip())]
    if query.lower().startswith("to:"):
        return ["TO", _quote(query[3:].strip())]
    if query.lower() == "unread":
        return ["UNSEEN"]

    # General keyword: search subject and from
    quoted = _quote(query)
    return ["OR", "SUBJECT", quoted, "FROM", quoted]


def _fetch_and_parse(conn: imaplib.IMAP4_SSL, msg_data: list) -> list[dict]:
    """Parse IMAP fetch response into email summary dicts."""
    emails = []
    i = 0
    while i < len(msg_data):
        if isinstance(msg_data[i], tuple):
            meta_line = (
                msg_data[i][0].decode("utf-8", errors="replace")
                if isinstance(msg_data[i][0], bytes)
                else str(msg_data[i][0])
            )
            uid_match = re.search(r"UID (\d+)", meta_line)
            uid = uid_match.group(1) if uid_match else ""
            is_read = r"\Seen" in meta_line

            raw_email = msg_data[i][1] if len(msg_data[i]) > 1 else b""
            # Combine header and text parts if available
            full_raw = raw_email
            j = i + 1
            while j < len(msg_data) and isinstance(msg_data[j], tuple):
                full_raw += msg_data[j][1] if len(msg_data[j]) > 1 else b""
                j += 1

            summary = _parse_summary(full_raw, uid)
            summary["is_read"] = is_read
            emails.append(summary)
        i += 1
    return emails


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_inbox(persona: PersonaConfig, count: int = 10) -> dict:
    """Get recent emails from inbox.

    Returns {"emails": [...], "total": int} or {"error": str}.
    """
    conn, err = _connect(persona)
    if err:
        return err

    try:
        status, data = conn.select("INBOX", readonly=True)
        if status != "OK":
            return {"error": "folder_error", "message": "Cannot open INBOX"}

        total = int(data[0])
        if total == 0:
            return {"emails": [], "total": 0}

        end = total
        start = max(1, end - count + 1)

        msg_range = f"{start}:{end}"
        status, msg_data = conn.fetch(msg_range, "(UID FLAGS BODY.PEEK[HEADER] BODY.PEEK[TEXT])")
        if status != "OK":
            return {"error": "fetch_failed", "message": "Failed to fetch emails"}

        emails = _fetch_and_parse(conn, msg_data)
        emails.reverse()  # Newest first
        return {"emails": emails, "total": total}

    except Exception as e:
        logger.error(f"IMAP get_inbox error for {persona.key}: {e}")
        return {"error": "fetch_failed", "message": "Failed to fetch emails"}
    finally:
        _disconnect(conn)


def search_emails(persona: PersonaConfig, query: str, count: int = 10) -> dict:
    """Search emails by subject, from, or keywords.

    Supports prefixes: from:, subject:, to:, unread.
    Returns {"emails": [...], "total": int} or {"error": str}.
    """
    conn, err = _connect(persona)
    if err:
        return err

    try:
        conn.select("INBOX", readonly=True)

        criteria = _build_search_criteria(query)
        status, data = conn.uid("search", None, *criteria)
        if status != "OK":
            return {"error": "search_failed", "message": "Email search failed"}

        uids = data[0].split() if data[0] else []
        uids = uids[-count:]
        uids.reverse()

        if not uids:
            return {"emails": [], "total": 0}

        uid_str = b",".join(uids)
        status, msg_data = conn.uid("fetch", uid_str, "(UID FLAGS BODY.PEEK[HEADER] BODY.PEEK[TEXT])")
        if status != "OK":
            return {"error": "search_failed", "message": "Failed to fetch search results"}

        emails = _fetch_and_parse(conn, msg_data)
        return {"emails": emails, "total": len(emails)}

    except Exception as e:
        logger.error(f"IMAP search error for {persona.key}: {e}")
        return {"error": "search_failed", "message": "Email search failed"}
    finally:
        _disconnect(conn)


def read_email(persona: PersonaConfig, uid: str) -> dict:
    """Read a specific email by UID. Returns full content.

    Returns dict with subject, from, to, cc, date, body_text, attachments,
    or {"error": str}.
    """
    conn, err = _connect(persona)
    if err:
        return err

    try:
        conn.select("INBOX", readonly=True)

        status, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK" or not msg_data or not msg_data[0]:
            return {"error": "fetch_failed", "message": "Email not found"}

        raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
        msg = email.message_from_bytes(raw)

        from_raw = msg.get("From", "")
        from_name, from_email_addr = email.utils.parseaddr(from_raw)

        body_text = _get_body_text(msg)

        # Collect attachment info
        attachments = []
        if msg.is_multipart():
            for part in msg.walk():
                filename = part.get_filename()
                if filename:
                    attachments.append({
                        "name": _decode_header(filename),
                        "content_type": part.get_content_type(),
                        "size": len(part.get_payload(decode=True) or b""),
                    })

        return {
            "uid": uid,
            "subject": _decode_header(msg.get("Subject", "(no subject)")),
            "from_name": _decode_header(from_name) or from_email_addr,
            "from_email": from_email_addr,
            "to": _parse_addresses(msg, "To"),
            "cc": _parse_addresses(msg, "Cc"),
            "date": _parse_date(msg.get("Date", "")),
            "body_text": body_text[:5000],
            "body_preview": body_text[:200],
            "has_attachments": len(attachments) > 0,
            "attachments": attachments,
        }

    except Exception as e:
        logger.error(f"IMAP read_email error for {persona.key}: {e}")
        return {"error": "fetch_failed", "message": "Failed to read email"}
    finally:
        _disconnect(conn)


def get_unread_count(persona: PersonaConfig) -> dict:
    """Get count of unread emails in inbox.

    Returns {"unread": int} or {"error": str}.
    """
    conn, err = _connect(persona)
    if err:
        return err

    try:
        conn.select("INBOX", readonly=True)
        status, data = conn.search(None, "UNSEEN")
        if status != "OK":
            return {"error": "fetch_failed", "message": "Failed to get unread count"}

        uids = data[0].split() if data[0] else []
        return {"unread": len(uids)}

    except Exception as e:
        logger.error(f"IMAP unread count error for {persona.key}: {e}")
        return {"error": "fetch_failed", "message": "Failed to get unread count"}
    finally:
        _disconnect(conn)
