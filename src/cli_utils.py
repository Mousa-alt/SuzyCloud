"""OpenSuzy — Shared Claude CLI utilities.

Provides common helpers for locating and invoking the Claude CLI executable.
Used by claude_runner.py (main conversation runner) and orchestrator.py
(background sub-agent execution) to avoid code duplication.

This module has NO src imports — it depends only on the Python standard library,
making it safe to import from anywhere without circular dependency risk.
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment variable cleanup
# ---------------------------------------------------------------------------
# The CLAUDECODE env var is set by an active Claude Code session.  When OpenSuzy
# spawns a *nested* Claude CLI subprocess, this var causes a "nested session"
# error.  Both _get_env() copies in claude_runner and orchestrator stripped it;
# this centralised version does the same.
_CLAUDECODE_ENV_VAR = "CLAUDECODE"


def find_claude_cmd() -> str:
    """Find the Claude CLI executable path.

    On Windows, looks for ``claude.cmd`` in the npm global directory
    (``%APPDATA%/npm``).  On other platforms, assumes ``claude`` is on PATH.

    Returns:
        Absolute path to the Claude CLI command, or ``"claude"`` as a
        fallback so that :func:`subprocess.run` can attempt PATH lookup.
    """
    if sys.platform == "win32":
        npm_dir = Path.home() / "AppData/Roaming/npm"
        claude_cmd = npm_dir / "claude.cmd"
        if claude_cmd.exists():
            return str(claude_cmd)
    return "claude"


def get_claude_env() -> dict:
    """Build a subprocess environment dict with Node/npm paths for the Claude CLI.

    Performs three things:

    1. Copies the current process environment.
    2. Removes the ``CLAUDECODE`` env var to prevent "nested session" errors
       when spawning Claude CLI as a subprocess.
    3. On Windows, prepends nvm-managed Node and npm directories to ``PATH``
       so that the Claude CLI (a Node package) can be found and executed.

    Returns:
        A :class:`dict` suitable for passing as the ``env`` parameter to
        :func:`subprocess.run` or :class:`subprocess.Popen`.
    """
    env = os.environ.copy()

    # Remove CLAUDECODE var to allow nested invocation
    env.pop(_CLAUDECODE_ENV_VAR, None)

    if sys.platform == "win32":
        extra_paths: list[str] = []

        # Ensure nvm-managed node and npm are in PATH
        nvm_home = os.environ.get("NVM_HOME", "")
        nvm_dir = Path(nvm_home) if nvm_home else Path.home() / "AppData" / "Local" / "nvm"
        if nvm_dir.exists():
            # Find the active/latest node version
            versions = sorted(
                [d for d in nvm_dir.iterdir() if d.is_dir() and d.name.startswith("v")],
                key=lambda d: d.name,
                reverse=True,
            )
            if versions:
                extra_paths.append(str(versions[0]))
                logger.debug(f"Using node from nvm: {versions[0]}")

        # Standard node paths
        for p in [
            Path("C:/Program Files/nodejs"),
            Path.home() / "AppData" / "Roaming" / "npm",
        ]:
            if p.exists():
                extra_paths.append(str(p))

        if extra_paths:
            env["PATH"] = ";".join(extra_paths) + ";" + env.get("PATH", "")

    return env


async def check_claude_auth() -> dict:
    """Check Claude CLI authentication status.

    Returns:
        Dict with ``logged_in`` (bool) and ``auth_method`` (str) keys.
    """
    cmd = find_claude_cmd()
    env = get_claude_env()
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd, "auth", "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            logger.warning(f"Claude auth status returned exit code {proc.returncode}")
            return {"logged_in": False, "auth_method": "error"}
        data = json.loads(stdout.decode().strip())
        return {
            "logged_in": data.get("loggedIn", False),
            "auth_method": data.get("authMethod", "none"),
        }
    except asyncio.TimeoutError:
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        logger.warning("Claude auth check timed out after 15s")
        return {"logged_in": False, "auth_method": "error"}
    except Exception:
        logger.exception("Claude auth check failed")
        return {"logged_in": False, "auth_method": "error"}
