"""OpenSuzy — Message batcher.

Groups rapid successive messages from the same sender before processing.
Ported from WhatsApp Task Manager's batcher.py, adapted for async callbacks.
"""

import asyncio
import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class MessageBatcher:
    """Buffers messages by (group_id, sender) and flushes after silence window.

    Usage:
        batcher = MessageBatcher(window_seconds=5, on_flush=process_batch)
        batcher.add(group_id, sender, sender_name, message_dict)
        # After 5s of silence from that sender...
        # on_flush(group_id, sender, sender_name, [msg1, msg2, ...]) is called
    """

    def __init__(self, window_seconds: float = 5.0,
                 on_flush: Optional[Callable] = None,
                 loop: Optional[asyncio.AbstractEventLoop] = None):
        self._window = window_seconds
        self._on_flush = on_flush
        self._loop = loop

        self._buffers: dict[tuple, list[dict]] = {}
        self._timers: dict[tuple, threading.Timer] = {}
        self._names: dict[tuple, str] = {}
        self._lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the event loop for async callback scheduling."""
        self._loop = loop

    def add(self, group_id: str, sender: str, sender_name: str,
            message: dict, window_override: float = 0) -> int:
        """Add a message to the buffer. Resets the flush timer.

        Returns current buffer size for this (group, sender) pair.
        """
        key = (group_id, sender)
        window = window_override if window_override > 0 else self._window

        with self._lock:
            if key not in self._buffers:
                self._buffers[key] = []

            self._buffers[key].append(message)
            self._names[key] = sender_name
            buffer_size = len(self._buffers[key])

            self._cancel_timer(key)
            self._start_timer(key, window)

            logger.info(
                f"Batcher: buffered msg #{buffer_size} from "
                f"{sender_name} in {group_id[:20]}... "
                f"(flush in {window}s)"
            )

        return buffer_size

    def flush_all(self) -> int:
        """Flush all pending buffers. Used during shutdown."""
        with self._lock:
            keys = list(self._buffers.keys())
            for key in keys:
                self._cancel_timer(key)

        count = 0
        for key in keys:
            group_id, sender = key
            with self._lock:
                messages = self._buffers.pop(key, None)
                sender_name = self._names.pop(key, "Unknown")

            if messages:
                self._fire_callback(group_id, sender, sender_name, messages)
                count += 1

        return count

    def pending_count(self) -> int:
        """Number of pending buffers."""
        with self._lock:
            return len(self._buffers)

    def _cancel_timer(self, key: tuple):
        """Cancel flush timer. Caller must hold _lock."""
        timer = self._timers.pop(key, None)
        if timer:
            timer.cancel()

    def _start_timer(self, key: tuple, window: float = 0):
        """Start new flush timer. Caller must hold _lock."""
        w = window if window > 0 else self._window
        timer = threading.Timer(w, self._on_timer, args=[key])
        timer.daemon = True
        timer.start()
        self._timers[key] = timer

    def _on_timer(self, key: tuple):
        """Called when silence window expires."""
        group_id, sender = key

        with self._lock:
            messages = self._buffers.pop(key, None)
            sender_name = self._names.pop(key, "Unknown")
            self._timers.pop(key, None)

        if messages:
            logger.info(
                f"Batcher: auto-flushed {len(messages)} msgs from "
                f"{sender_name} after {self._window}s silence"
            )
            self._fire_callback(group_id, sender, sender_name, messages)

    @staticmethod
    def _handle_flush_task_result(task: asyncio.Task):
        """Done callback for flush tasks — log exceptions that would otherwise be lost."""
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.error(
                f"Batcher flush task failed: {exc}",
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    def _fire_callback(self, group_id: str, sender: str,
                       sender_name: str, messages: list[dict]):
        """Execute flush callback. Schedules async callback on event loop."""
        if not self._on_flush:
            logger.warning("Batcher: no on_flush callback configured")
            return

        try:
            if self._loop and asyncio.iscoroutinefunction(self._on_flush):
                def _schedule_with_error_handling():
                    task = asyncio.ensure_future(
                        self._on_flush(group_id, sender, sender_name, messages),
                    )
                    task.add_done_callback(self._handle_flush_task_result)
                self._loop.call_soon_threadsafe(_schedule_with_error_handling)
            else:
                self._on_flush(group_id, sender, sender_name, messages)
        except Exception as e:
            logger.error(f"Batcher flush callback error: {e}", exc_info=True)


def merge_batched_messages(messages: list[dict]) -> dict:
    """Merge multiple buffered messages into a single processing unit.

    Returns dict with: combined_text, media_items, message_ids,
    first_message_id, first_full_message_id, quoted_texts
    """
    texts = []
    media_items = []
    message_ids = []
    quoted_texts = []

    for msg in messages:
        if msg.get("text"):
            texts.append(msg["text"])
        if msg.get("media_info"):
            media_items.append(msg["media_info"])
        if msg.get("message_id"):
            message_ids.append(msg["message_id"])
        # Extract quoted/replied-to message text
        quoted = msg.get("quoted_msg")
        if quoted and isinstance(quoted, dict):
            q_text = quoted.get("body") or quoted.get("text") or quoted.get("caption") or ""
            if q_text:
                quoted_texts.append(q_text)

    return {
        "combined_text": "\n".join(texts),
        "media_items": media_items,
        "message_ids": message_ids,
        "first_message_id": messages[0].get("message_id", ""),
        "first_full_message_id": messages[0].get("full_message_id", ""),
        "quoted_texts": quoted_texts,
    }
