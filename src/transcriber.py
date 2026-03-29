"""SuzyCloud — Local Whisper voice note transcriber.

Uses faster-whisper for zero-cost local transcription.
Model loads lazily on first use with thread-safe initialization.
"""

import asyncio
import logging
import tempfile
import threading
from pathlib import Path
from typing import Optional

from src import config

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()

# MIME type to file extension mapping
_EXT_MAP = {
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
}


def _get_model():
    """Load Whisper model lazily on first use (thread-safe)."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:  # double-check after acquiring lock
                logger.info(
                    f"Loading Whisper model '{config.WHISPER_MODEL_SIZE}' "
                    f"on {config.WHISPER_DEVICE}..."
                )
                try:
                    from faster_whisper import WhisperModel

                    _model = WhisperModel(
                        config.WHISPER_MODEL_SIZE,
                        device=config.WHISPER_DEVICE,
                        compute_type=(
                            "int8" if config.WHISPER_DEVICE == "cpu" else "float16"
                        ),
                    )
                    logger.info("Whisper model loaded successfully")
                except Exception as e:
                    logger.error(f"Failed to load Whisper model: {e}")
                    raise
    return _model


async def transcribe(audio_path: str) -> str:
    """Transcribe audio file using Whisper. Returns text.

    Args:
        audio_path: Path to the audio file on disk.

    Returns:
        Transcribed text, or empty string if transcription fails.
    """
    if not Path(audio_path).exists():
        logger.error(f"Audio file not found: {audio_path}")
        return ""

    try:
        # Run blocking transcription in a thread pool
        text = await asyncio.get_event_loop().run_in_executor(
            None, _transcribe_sync, audio_path
        )
        return text
    except Exception as e:
        logger.error(f"Transcription error: {e}", exc_info=True)
        return ""


def _transcribe_sync(audio_path: str) -> str:
    """Synchronous transcription (runs in thread pool)."""
    model = _get_model()

    language = config.WHISPER_LANGUAGE if config.WHISPER_LANGUAGE != "auto" else None

    segments, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        vad_filter=True,
        initial_prompt="مصري عامية" if language == "ar" else None,
    )

    detected_lang = info.language
    confidence = info.language_probability
    duration = info.duration

    # Re-transcribe with Egyptian dialect hint if Arabic detected
    if language is None and detected_lang == "ar":
        logger.info("Arabic detected — re-transcribing with dialect hint")
        segments, info = model.transcribe(
            audio_path,
            language="ar",
            beam_size=5,
            vad_filter=True,
            initial_prompt="مصري عامية، ازيك، كويس، عايز، يعني، اه، طيب",
        )
        confidence = info.language_probability
        duration = info.duration

    text = " ".join(seg.text.strip() for seg in segments).strip()

    if text:
        logger.info(
            f"Transcribed {audio_path} → {len(text)} chars "
            f"({detected_lang}, conf={confidence:.2f}, dur={duration:.1f}s)"
        )
    else:
        logger.warning(f"Transcription returned empty text for {audio_path}")

    return text


def is_loaded() -> bool:
    """Check if Whisper model is loaded."""
    return _model is not None
