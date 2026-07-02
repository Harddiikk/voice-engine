"""Generate short spoken previews for realtime (speech-to-speech) voices.

Pipeline TTS providers ship a browsable voice catalog with hosted preview
clips (see ``GET /user/configurations/voices/{provider}``). Realtime
providers have no such catalog, so we synthesize a short sample using the
organization's own API key and cache the resulting WAV in object storage.
"""

from __future__ import annotations

import base64
import io
import re
import wave

import httpx
from fastapi import HTTPException
from loguru import logger

from api.services.configuration.ai_model_configuration import (
    get_resolved_ai_model_configuration,
)
from api.services.storage import storage_fs

# Short, warm agent-style greeting per language; falls back to English.
PREVIEW_TEXTS: dict[str, str] = {
    "en": (
        "Hello! I'm your AI voice assistant. "
        "This is how I will sound on your calls."
    ),
    "hi": (
        "नमस्ते! मैं आपकी वॉइस असिस्टेंट हूँ। "
        "आपकी कॉल्स पर मेरी आवाज़ ऐसी सुनाई देगी।"
    ),
}

SIGNED_URL_EXPIRY_SECONDS = 3600

# Gemini Live models cannot do one-shot TTS over REST, so previews use the
# dedicated Gemini TTS preview model with the same prebuilt voice names.
_GEMINI_TTS_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash-preview-tts:generateContent"
)
# Gemini TTS returns raw PCM: signed 16-bit little-endian, 24 kHz, mono.
_GEMINI_PCM_SAMPLE_RATE = 24000
_GEMINI_PCM_SAMPLE_WIDTH_BYTES = 2
_GEMINI_PCM_CHANNELS = 1

_OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"

_HTTP_TIMEOUT_SECONDS = 30.0


def preview_text_for_language(language: str | None) -> str:
    base = (language or "en").split("-")[0].strip().lower()
    return PREVIEW_TEXTS.get(base, PREVIEW_TEXTS["en"])


def _sanitize_key_component(component: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", component)


def preview_cache_key(
    provider: str, model: str, voice: str, language: str | None
) -> str:
    parts = (provider, model, voice, language or "en")
    return "voice-previews/{}/{}/{}/{}.wav".format(
        *(_sanitize_key_component(part) for part in parts)
    )


def pcm_s16le_to_wav(pcm: bytes) -> bytes:
    """Wrap raw Gemini PCM (s16le / 24 kHz / mono) in a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(_GEMINI_PCM_CHANNELS)
        wav_file.setsampwidth(_GEMINI_PCM_SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(_GEMINI_PCM_SAMPLE_RATE)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


async def get_realtime_voice_preview(
    *,
    user_id: int | None,
    organization_id: int | None,
    provider: str,
    voice: str,
    language: str | None = None,
    model: str | None = None,
) -> dict:
    """Return ``{"url": <signed url>, "cached": <bool>}`` for a voice sample.

    Resolves the caller's effective model configuration, requires a realtime
    section matching ``provider`` (the preview is synthesized with the org's
    own API key), and caches generated WAVs in object storage.
    """
    resolved = await get_resolved_ai_model_configuration(
        user_id=user_id,
        organization_id=organization_id,
    )
    effective = resolved.effective
    realtime = effective.realtime
    if not effective.is_realtime or realtime is None:
        raise HTTPException(
            status_code=422,
            detail="Voice preview requires a realtime model configuration",
        )
    if realtime.provider != provider:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Configured realtime provider is '{realtime.provider}', "
                f"not '{provider}'"
            ),
        )
    api_keys = (
        realtime.get_all_api_keys() if hasattr(realtime, "get_all_api_keys") else []
    )
    if not api_keys:
        raise HTTPException(
            status_code=422,
            detail="No API key configured for the realtime provider",
        )
    api_key = api_keys[0]

    effective_model = model or getattr(realtime, "model", None) or "default"
    cache_key = preview_cache_key(provider, effective_model, voice, language)

    metadata = await storage_fs.aget_file_metadata(cache_key)
    if metadata:
        url = await storage_fs.aget_signed_url(
            cache_key, expiration=SIGNED_URL_EXPIRY_SECONDS
        )
        if not url:
            raise HTTPException(
                status_code=502, detail="Preview generation failed"
            )
        return {"url": url, "cached": True}

    text = preview_text_for_language(language)
    if provider == "google_realtime":
        wav_bytes = await _generate_google_preview(
            api_key=api_key, voice=voice, text=text
        )
    elif provider == "openai_realtime":
        wav_bytes = await _generate_openai_preview(
            api_key=api_key, voice=voice, text=text
        )
    else:
        raise HTTPException(
            status_code=422,
            detail=f"Voice preview is not available for {provider}",
        )

    created = await storage_fs.acreate_file(cache_key, io.BytesIO(wav_bytes))
    if not created:
        logger.error(f"Failed to store voice preview at {cache_key}")
        raise HTTPException(status_code=502, detail="Preview generation failed")

    url = await storage_fs.aget_signed_url(
        cache_key, expiration=SIGNED_URL_EXPIRY_SECONDS
    )
    if not url:
        raise HTTPException(status_code=502, detail="Preview generation failed")
    return {"url": url, "cached": False}


async def _generate_google_preview(*, api_key: str, voice: str, text: str) -> bytes:
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
            },
        },
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _GEMINI_TTS_URL,
                headers={"x-goog-api-key": api_key},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        audio_b64 = body["candidates"][0]["content"]["parts"][0]["inlineData"][
            "data"
        ]
        pcm = base64.b64decode(audio_b64)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — upstream details must not leak
        logger.error(f"Google realtime voice preview failed for '{voice}': {exc}")
        raise HTTPException(status_code=502, detail="Preview generation failed")
    if not pcm:
        logger.error(f"Google realtime voice preview returned no audio for '{voice}'")
        raise HTTPException(status_code=502, detail="Preview generation failed")
    return pcm_s16le_to_wav(pcm)


async def _generate_openai_preview(*, api_key: str, voice: str, text: str) -> bytes:
    payload = {
        "model": _OPENAI_TTS_MODEL,
        "voice": voice,
        "input": text,
        "response_format": "wav",
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _OPENAI_SPEECH_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            response.raise_for_status()
            wav_bytes = response.content
    except Exception as exc:  # noqa: BLE001 — upstream details must not leak
        logger.error(f"OpenAI realtime voice preview failed for '{voice}': {exc}")
        raise HTTPException(status_code=502, detail="Preview generation failed")
    if not wav_bytes:
        logger.error(f"OpenAI realtime voice preview returned no audio for '{voice}'")
        raise HTTPException(status_code=502, detail="Preview generation failed")
    return wav_bytes
