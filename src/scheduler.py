"""SuzyCloud — Per-persona scheduler.

Uses APScheduler to run per-persona scheduled jobs. Each persona can have
different morning briefing times, timezones, and feature flags.

Also runs a global reminder checker every minute across all personas.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src import config
from src.persona import PersonaConfig
from src.soul_loader import load_soul_context

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_waha_client = None


def _get_tz(timezone: str) -> ZoneInfo:
    """Resolve timezone string to ZoneInfo, falling back to Africa/Cairo."""
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, KeyError):
        return ZoneInfo("Africa/Cairo")


async def _morning_briefing(persona: PersonaConfig) -> None:
    """Run morning briefing for a single persona."""
    if _waha_client is None:
        logger.error(f"[{persona.key}] Cannot send briefing — no waha client")
        return

    if not persona.group_ids:
        logger.warning(f"[{persona.key}] No group_ids configured, skipping briefing")
        return

    logger.info(f"[{persona.key}] Starting morning briefing")

    try:
        # Load full soul context for this persona
        context = load_soul_context(persona)

        # Ask Claude for a morning briefing
        from src.claude_runner import ask_claude

        prompt = (
            "Good morning. Check the calendar, email, and tasks, "
            "then send a morning briefing."
        )
        response = await ask_claude(
            persona=persona,
            message=prompt,
            context=context,
        )

        if response:
            # Send to the persona's first group
            target = persona.group_ids[0]
            await _waha_client.send_text(target, response)
            logger.info(
                f"[{persona.key}] Morning briefing sent to {target} "
                f"({len(response)} chars)"
            )
        else:
            logger.warning(f"[{persona.key}] Claude returned empty briefing")

    except Exception as e:
        logger.error(f"[{persona.key}] Morning briefing failed: {e}", exc_info=True)


async def _check_reminders() -> None:
    """Scan all persona data dirs for reminders.json and fire due reminders."""
    if _waha_client is None:
        return

    personas_dir = config.PERSONAS_DIR
    if not personas_dir.exists():
        return

    now_utc = datetime.now(ZoneInfo("UTC"))

    for persona_dir in personas_dir.iterdir():
        if not persona_dir.is_dir():
            continue

        reminders_file = persona_dir / "data" / "reminders.json"
        if not reminders_file.exists():
            continue

        try:
            reminders = json.loads(reminders_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to read {reminders_file}: {e}")
            continue

        if not isinstance(reminders, list) or not reminders:
            continue

        remaining = []
        fired = 0

        for reminder in reminders:
            try:
                fire_at = datetime.fromisoformat(reminder["time"])
                # Ensure timezone-aware
                if fire_at.tzinfo is None:
                    fire_at = fire_at.replace(tzinfo=ZoneInfo("Africa/Cairo"))

                if now_utc >= fire_at.astimezone(ZoneInfo("UTC")):
                    # Fire the reminder
                    target = reminder.get("group_id", "")
                    text = reminder.get("text", "Reminder!")
                    if target:
                        await _waha_client.send_text(
                            target, f"*Reminder* {text}"
                        )
                        fired += 1
                        logger.info(
                            f"[{persona_dir.name}] Fired reminder: {text[:50]}"
                        )
                else:
                    remaining.append(reminder)
            except (KeyError, ValueError) as e:
                logger.warning(f"Invalid reminder entry in {persona_dir.name}: {e}")
                continue

        if fired:
            # Write back remaining reminders
            reminders_file.write_text(
                json.dumps(remaining, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(
                f"[{persona_dir.name}] Fired {fired} reminder(s), "
                f"{len(remaining)} remaining"
            )


async def start(waha_client, personas: list[PersonaConfig]) -> None:
    """Start scheduler with per-persona jobs."""
    global _scheduler, _waha_client

    if _scheduler is not None:
        logger.warning("Scheduler already running")
        return

    _waha_client = waha_client
    _scheduler = AsyncIOScheduler(timezone="UTC")

    # Per-persona morning briefings
    for persona in personas:
        if not persona.morning_briefing_enabled:
            logger.info(f"[{persona.key}] Morning briefing disabled, skipping")
            continue

        # Parse "HH:MM" time
        try:
            hour, minute = map(int, persona.morning_briefing.split(":"))
        except (ValueError, AttributeError):
            logger.error(
                f"[{persona.key}] Invalid morning_briefing time: "
                f"{persona.morning_briefing!r}, skipping"
            )
            continue

        tz = _get_tz(persona.timezone)

        _scheduler.add_job(
            _morning_briefing,
            CronTrigger(hour=hour, minute=minute, timezone=tz),
            args=[persona],
            id=f"briefing_{persona.key}",
            name=f"Morning briefing — {persona.name}",
            misfire_grace_time=900,
            coalesce=True,
        )
        logger.info(
            f"[{persona.key}] Morning briefing scheduled at "
            f"{hour:02d}:{minute:02d} ({persona.timezone})"
        )

    # Global reminder checker — every minute
    _scheduler.add_job(
        _check_reminders,
        IntervalTrigger(minutes=1),
        id="reminder_checker",
        name="Global reminder checker",
        misfire_grace_time=60,
        coalesce=True,
    )
    logger.info("Reminder checker scheduled (every 60s)")

    _scheduler.start()
    logger.info(
        f"Scheduler started with {len(_scheduler.get_jobs())} job(s)"
    )


async def stop() -> None:
    """Shutdown scheduler."""
    global _scheduler, _waha_client

    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
        _scheduler = None

    _waha_client = None
