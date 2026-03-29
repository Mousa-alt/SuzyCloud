"""OpenSuzy — Claude Code CLI subprocess runner.

Invokes `claude -p` with session resumption and soul context injection.
Uses subprocess.run in a thread to avoid Windows asyncio subprocess issues.

Message and soul context are piped via stdin to avoid Windows command line
length limits (8191 chars).
"""

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

from src import config
from src.cli_utils import find_claude_cmd as _find_claude_cmd, get_claude_env as _get_env

logger = logging.getLogger(__name__)


def _git_snapshot(cwd: str) -> Optional[str]:
    """Create a git safety snapshot before Claude modifies a project.

    If the directory is a git repo, stashes or commits uncommitted changes
    and tags the current state so the user can always roll back.

    Returns the snapshot tag name, or None if not a git repo.
    """
    from datetime import datetime

    repo_path = Path(cwd)
    if not (repo_path / ".git").exists():
        return None

    try:
        env = _get_env()
        timestamp = config.cairo_now().strftime("%Y%m%d-%H%M%S")
        tag_name = f"suzy-snapshot-{timestamp}"

        # Tag current HEAD so we can always find this point
        subprocess.run(
            ["git", "tag", tag_name],
            capture_output=True, cwd=cwd, env=env, timeout=10,
        )

        logger.info(f"Git snapshot: {tag_name} in {cwd}")
        return tag_name

    except Exception as e:
        logger.warning(f"Git snapshot failed (non-fatal): {e}")
        return None


def _run_claude_sync(args: list[str], stdin_text: str = "", cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run Claude CLI synchronously (called from thread).

    Uses Popen instead of run() so we can explicitly kill the process on
    timeout — subprocess.run() leaves the child alive on TimeoutExpired,
    causing zombie process accumulation and eventual OOM.
    """
    args = [_find_claude_cmd()] + args[1:]
    process = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd or str(config.PROJECT_ROOT),
        env=_get_env(),
    )
    try:
        stdout, stderr = process.communicate(
            input=stdin_text.encode("utf-8") if stdin_text else None,
            timeout=config.CLAUDE_TIMEOUT,
        )
        return subprocess.CompletedProcess(
            args, process.returncode, stdout, stderr,
        )
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()  # reap the zombie
        raise


async def _try_fallbacks(message: str, soul_context: str) -> Optional[str]:
    """Attempt Gemini fallback when Claude CLI fails."""
    if config.GEMINI_FALLBACK_ENABLED:
        from src import gemini_fallback
        logger.info("Claude CLI failed — attempting Gemini fallback...")
        fallback_text = await gemini_fallback.generate(message, soul_context)
        if fallback_text:
            model_name = gemini_fallback.get_current_model()
            notification = f"[System: ⚠️ Claude CLI limit hit. Now using {model_name}]\n\n"
            return notification + fallback_text

    return None


async def run(
    message: str,
    session_id: Optional[str] = None,
    soul_context: str = "",
    cwd: Optional[str] = None,
    model: Optional[str] = None,
    is_resumed: bool = False,
    max_turns: Optional[int] = None,
    _retry_fresh: bool = False,
    _skip_empty_followup: bool = False,
) -> tuple[str, Optional[str]]:
    """Run a message through Claude Code CLI.

    The message and soul context are piped via stdin to avoid Windows
    command line length limits. The soul context is prepended as a
    system instruction block.

    If cwd is provided, Claude CLI runs in that directory (for multi-project routing).
    Falls back to Gemini SDK if the CLI fails and fallback is enabled.
    """
    # -p without a value = read prompt from stdin
    args = [
        "claude",
        "-p",
        "--output-format", "json",
        "--max-turns", str(max_turns or config.CLAUDE_MAX_TURNS),
        "--permission-mode", "bypassPermissions",
    ]

    if session_id and not _retry_fresh:
        args += ["--resume", session_id]

    # Always pass budget cap to override CLI's default (which is too low for tool queries).
    # Config value should be >0 (e.g. 5.0). Subscription users don't pay per-call.
    budget = config.CLAUDE_MAX_BUDGET or 5.0
    args += ["--max-budget-usd", str(budget)]

    # Use provided model, or fall back to chat model
    effective_model = model or config.CLAUDE_CHAT_MODEL
    if effective_model:
        args += ["--model", effective_model]

    # Fresh retry after thinking budget exhaustion uses medium effort to avoid looping.
    # All other requests use default (high) effort — no cost on subscription.
    if _retry_fresh:
        args += ["--effort", "medium"]

    # Build stdin: soul context + message combined.
    if soul_context:
        stdin_text = (
            f"<system-instructions>\n{soul_context}\n</system-instructions>\n\n"
            f"{message}"
        )
    else:
        stdin_text = message

    context_label = "fresh retry" if _retry_fresh else (
        "memory refresh" if is_resumed and soul_context else ("full soul" if soul_context else "no context")
    )
    logger.info(
        f"Claude CLI: {'FRESH RETRY' if _retry_fresh else ('resuming ' + session_id[:8] + '...' if session_id else 'new session')} "
        f"| context={context_label} ({len(soul_context)} chars) | message: {message[:80]}..."
    )

    try:
        # Safety snapshot: tag the current state so user can always rollback
        effective_cwd = cwd or str(config.PROJECT_ROOT)
        if not _retry_fresh:
            snapshot_tag = await asyncio.to_thread(_git_snapshot, effective_cwd)
            if snapshot_tag:
                logger.info(f"Safety snapshot created: {snapshot_tag} — run `git checkout {snapshot_tag}` to rollback")

            # Self-modification safety: backup critical files before modifying OpenSuzy
            from src import safety
            if safety.is_self_modification(cwd):
                backup_id = await asyncio.to_thread(safety.backup_critical_files)
                if backup_id:
                    logger.info(f"Self-modification detected — safety backup: {backup_id}")

        result = await asyncio.to_thread(_run_claude_sync, args, stdin_text, cwd)

        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
            stdout_text = result.stdout.decode("utf-8", errors="replace").strip()
            logger.error(f"Claude CLI error (exit {result.returncode}): {stderr_text[:500]}")

            # Try to parse JSON even on non-zero RC — CLI returns useful error info
            error_result = None
            error_session = session_id
            try:
                data = json.loads(stdout_text or stderr_text)
                error_result = data.get("result", "")
                error_session = data.get("session_id", session_id)
            except (json.JSONDecodeError, ValueError):
                pass

            # Detect rate/subscription limits from stderr/stdout/parsed result
            combined_err = (stderr_text + " " + stdout_text).lower()
            # Also check the parsed result text for limit messages
            err_lower = (error_result or "").lower()
            is_rate_limit = any(kw in combined_err or kw in err_lower for kw in [
                "rate limit", "rate_limit", "429", "overloaded",
                "too many requests", "usage limit", "hit your limit", "hit your usage",
                "resets", "you've hit",
            ])
            if is_rate_limit:
                from src.telemetry import record_rate_limit_hit
                import re
                reset_match = re.search(r"(\d+)\s*(?:hour|hr)", combined_err)
                reset_seconds = int(reset_match.group(1)) * 3600 if reset_match else 18000
                record_rate_limit_hit(reset_seconds)
                logger.warning(f"Rate limit detected! Reset in ~{reset_seconds // 3600}h. Error: {error_result or stderr_text[:200]}")
                # Forward the actual rate limit message to the user
                if error_result:
                    return (f"⏳ {error_result}", error_session)
                return ("⏳ You've hit the Claude usage limit. Try again later.", error_session)

            debug_path = config.PROJECT_ROOT / "_claude_debug.txt"
            debug_path.write_text(
                f"RC: {result.returncode}\n"
                f"STDERR: {stderr_text[:2000]}\n"
                f"STDOUT: {stdout_text[:2000]}\n"
                f"ARGS: {args}\n"
                f"STDIN_LEN: {len(stdin_text)}\n"
                f"ENV_PATH: {_get_env().get('PATH', '')[:500]}\n",
                encoding="utf-8",
            )
            # Attempt fallbacks before giving up
            fallback = await _try_fallbacks(message, soul_context)
            if fallback:
                return (fallback, session_id)
            # If we have a parsed error message from CLI, forward it
            if error_result:
                return (error_result, error_session)
            return (
                "Something went wrong on my end. Try again in a moment.",
                session_id,
            )

        stdout_text = result.stdout.decode("utf-8", errors="replace").strip()
        stderr_text = result.stderr.decode("utf-8", errors="replace").strip()

        # Claude CLI may write --output-format json to stdout or stderr
        raw_json = stdout_text or stderr_text
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            logger.warning(f"Claude CLI returned non-JSON output (stdout={len(stdout_text)}, stderr={len(stderr_text)})")
            return (stdout_text[:4000] or stderr_text[:4000], session_id)

        result_text = data.get("result", "")
        new_session_id = data.get("session_id", session_id)
        subtype = data.get("subtype", "")
        is_error = data.get("is_error", False)

        # --- Thinking budget exhaustion (check FIRST, before general is_error) ---
        # Happens when resumed session context is too large, leaving no room
        # for extended thinking.  Auto-retry with a fresh session so the user
        # gets a response instead of an error.
        _thinking_budget_markers = ("ran out of thinking budget", "thinking budget")
        _is_thinking_budget = result_text and any(
            m in result_text.lower() for m in _thinking_budget_markers
        )
        if _is_thinking_budget:
            if not _retry_fresh:
                logger.warning(
                    f"Thinking budget exhausted"
                    f"{' on session ' + session_id[:8] + '...' if session_id else ''}"
                    f" — retrying with fresh session (medium effort)"
                )
                # Keep soul context compact for the retry — full soul caused
                # the thinking budget issue in the first place. Only reload
                # if the current context is suspiciously small (< 500 chars).
                retry_soul = soul_context
                if len(soul_context) < 500:
                    try:
                        from src import groups
                        retry_soul = groups.load_soul_context(message_text=message)
                    except Exception:
                        pass
                return await run(
                    message, session_id=None, soul_context=retry_soul,
                    cwd=cwd, model=model, is_resumed=False,
                    max_turns=max_turns, _retry_fresh=True,
                )
            # Already retried fresh — don't loop, try fallback then give up
            logger.error("Thinking budget exhausted even on fresh session")
            fallback = await _try_fallbacks(message, soul_context)
            if fallback:
                return (fallback, new_session_id)
            return (
                "I'm having trouble processing that right now. Try again?",
                None,  # force new session next time
            )

        # Claude CLI can return RC=0 but is_error=true (e.g. subscription limit).
        # Detect rate/subscription limits and forward the real message.
        if is_error and result_text:
            logger.warning(f"Claude CLI returned is_error=true: {result_text[:200]}")
            _limit_keywords = ("hit your limit", "usage limit", "rate limit", "resets")
            if any(kw in result_text.lower() for kw in _limit_keywords):
                return (f"⏳ {result_text}", new_session_id)
            fallback = await _try_fallbacks(message, soul_context)
            if fallback:
                return (fallback, new_session_id)
            return (result_text, new_session_id)

        if not result_text:
            cost = data.get("total_cost_usd", 0)
            turns = data.get("num_turns", 0)
            denials = data.get("permission_denials", [])

            if subtype == "error_max_budget_usd":
                logger.error(f"Claude CLI hit budget limit: ${cost:.2f} spent, {turns} turns")
                return (
                    f"⏳ I hit my usage limit (${cost:.2f} spent, {turns} turns). "
                    "Try again shortly or with a simpler request.",
                    new_session_id,
                )
            elif subtype == "error_max_turns":
                logger.error(f"Claude CLI hit max turns: {turns} turns used")
                return (
                    f"That took more steps than allowed ({turns} turns). "
                    "Try breaking it into smaller requests.",
                    new_session_id,
                )
            elif denials:
                logger.error(f"Claude CLI permission denials: {denials}")
                return (
                    "I wanted to help but I don't have permission for some actions I need.",
                    new_session_id,
                )
            else:
                # Claude did work (turns > 0) but ended on a tool call
                # with no final text response.  Follow up on the same
                # session to extract a summary.
                if turns > 0 and new_session_id and not _skip_empty_followup:
                    logger.info(
                        f"Claude CLI empty result after {turns} turns — "
                        f"following up on session {new_session_id[:8]}..."
                    )
                    followup_text, followup_sid = await run(
                        "You completed actions but didn't send a reply to the user. "
                        "Summarize what you did or respond to the user now.",
                        session_id=new_session_id,
                        soul_context="",  # no soul context needed
                        cwd=cwd,
                        model=model,
                        max_turns=2,
                        _skip_empty_followup=True,  # prevents infinite recursion
                    )
                    if followup_text and "nothing to say" not in followup_text.lower():
                        return (followup_text, followup_sid or new_session_id)

                logger.warning(
                    f"Claude CLI returned empty result "
                    f"(subtype={subtype}, cost=${cost:.2f}, turns={turns})"
                )
                return ("I processed your message but had nothing to say.", new_session_id)

        logger.info(
            f"Claude CLI: response {len(result_text)} chars, "
            f"session {new_session_id[:8] if new_session_id else 'none'}..."
        )

        # Change summary disabled — was producing stale false positives

        return (result_text, new_session_id)

    except subprocess.TimeoutExpired:
        logger.error(f"Claude CLI timed out after {config.CLAUDE_TIMEOUT}s")
        # Attempt fallbacks on timeout
        fallback = await _try_fallbacks(message, soul_context)
        if fallback:
            return (fallback, session_id)
        return (
            "Sorry, I took too long thinking about that. Try again with a simpler question?",
            session_id,
        )
    except FileNotFoundError:
        logger.error("Claude CLI not found. Is 'claude' in PATH?")
        # Attempt fallbacks if CLI is broken / missing
        fallback = await _try_fallbacks(message, soul_context)
        if fallback:
            return (fallback, session_id)
        return (
            "I'm having trouble starting up. The claude command isn't available.",
            session_id,
        )
    except Exception as e:
        logger.error(f"Claude CLI unexpected error: {e}", exc_info=True)
        return (
            "Something unexpected went wrong. Try again?",
            session_id,
        )
