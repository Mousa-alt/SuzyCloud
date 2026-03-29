"""SuzyCloud — Multi-tenant configuration.

Each persona has its own credentials and settings. Global config handles
shared infrastructure (Waha, webhook port, etc.). Per-persona config
lives in personas/{key}/config.yaml + .env files.
"""

import logging
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load global .env
_SECURE_ENV = Path.home() / ".suzycloud" / ".env"
load_dotenv(_SECURE_ENV if _SECURE_ENV.exists() else PROJECT_ROOT / ".env")

# --- Global infrastructure secrets ---
WAHA_API_URL: str = os.environ.get("WAHA_API_URL", "")
WAHA_API_KEY: str = os.environ.get("WAHA_API_KEY", "")
WAHA_SESSION: str = os.environ.get("WAHA_SESSION", "default")
WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")
DASHBOARD_SECRET: str = os.environ.get("DASHBOARD_SECRET", "")

# --- Global config.yaml ---
_config_path = PROJECT_ROOT / "config.yaml"
_cfg: dict = {}
if _config_path.exists():
    with open(_config_path, "r", encoding="utf-8") as f:
        _cfg = yaml.safe_load(f) or {}

WEBHOOK_PORT: int = _cfg.get("webhook", {}).get("port", 8000)
BATCH_WINDOW_SECONDS: float = _cfg.get("batching", {}).get("window_seconds", 5.0)
MAX_REQUESTS_PER_MINUTE: int = _cfg.get("rate_limit", {}).get("max_per_minute", 20)

# Whisper (shared — runs on VPS CPU)
_whisper_cfg = _cfg.get("whisper", {})
WHISPER_MODEL_SIZE: str = _whisper_cfg.get("model_size", "small")
WHISPER_DEVICE: str = _whisper_cfg.get("device", "cpu")
WHISPER_LANGUAGE: str = _whisper_cfg.get("language", "auto")

# Gateway (shared Baileys instance)
_gw_cfg = _cfg.get("gateway", {})
GATEWAY_URL: str = _gw_cfg.get("url", "http://localhost:3001")
GATEWAY_API_KEY: str = os.environ.get("GATEWAY_API_KEY", "")
GATEWAY_ENABLED: bool = _gw_cfg.get("enabled", True)

# --- Paths ---
PERSONAS_DIR = PROJECT_ROOT / "personas"
SESSIONS_FILE = PROJECT_ROOT / "data" / "sessions.json"
MEDIA_DIR = PROJECT_ROOT / "media"

# --- Timezone ---
TIMEZONE: str = _cfg.get("timezone", "Africa/Cairo")


def cairo_now():
    """Timezone-aware now() in configured timezone."""
    from datetime import datetime
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        tz = ZoneInfo(TIMEZONE)
    except (ZoneInfoNotFoundError, KeyError):
        tz = ZoneInfo("Africa/Cairo")
    return datetime.now(tz)


def cairo_today():
    """date.today() in configured timezone."""
    return cairo_now().date()


def setup_logging():
    """Configure logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
