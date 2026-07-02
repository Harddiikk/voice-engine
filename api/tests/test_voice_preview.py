"""Tests for the realtime voice preview service (storage + httpx mocked)."""

import base64
import io
import wave
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration import voice_preview
from api.services.configuration.ai_model_configuration import (
    ResolvedAIModelConfiguration,
)
from api.services.configuration.registry import (
    GoogleRealtimeLLMConfiguration,
    GrokRealtimeLLMConfiguration,
    OpenAILLMService,
)

PCM_SAMPLE = b"\x01\x02\x03\x04" * 32  # tiny fake s16le PCM payload


def _realtime_effective(realtime=None) -> EffectiveAIModelConfiguration:
    return EffectiveAIModelConfiguration(
        llm=OpenAILLMService(api_key="llm-key", model="gpt-4.1"),
        realtime=realtime
        or GoogleRealtimeLLMConfiguration(
            api_key="google-key",
            model="gemini-3.1-flash-live-preview",
            voice="Kore",
            language="hi",
        ),
        is_realtime=True,
    )


class FakeStorage:
    def __init__(self, existing_metadata=None):
        self.aget_file_metadata = AsyncMock(return_value=existing_metadata)
        self.acreate_file = AsyncMock(return_value=True)
        self.aget_signed_url = AsyncMock(return_value="https://signed.example/preview")


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json = json_data
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "https://upstream.example"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._json


class FakeAsyncClient:
    """Stands in for httpx.AsyncClient; records post calls."""

    calls: list = []
    response: FakeResponse = FakeResponse()

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        FakeAsyncClient.calls.append({"url": url, **kwargs})
        return FakeAsyncClient.response


@pytest.fixture(autouse=True)
def _reset_fake_client():
    FakeAsyncClient.calls = []
    FakeAsyncClient.response = FakeResponse()


@pytest.fixture
def patch_env(monkeypatch):
    def _patch(*, effective, storage, response=None):
        monkeypatch.setattr(
            voice_preview,
            "get_resolved_ai_model_configuration",
            AsyncMock(
                return_value=ResolvedAIModelConfiguration(
                    effective=effective, source="organization_v2"
                )
            ),
        )
        monkeypatch.setattr(voice_preview, "storage_fs", storage)
        if response is not None:
            FakeAsyncClient.response = response
        monkeypatch.setattr(voice_preview.httpx, "AsyncClient", FakeAsyncClient)
        return storage

    return _patch


async def test_cache_hit_returns_signed_url_without_upstream_call(patch_env):
    storage = FakeStorage(existing_metadata={"size": 123})
    patch_env(effective=_realtime_effective(), storage=storage)

    result = await voice_preview.get_realtime_voice_preview(
        user_id=1,
        organization_id=1,
        provider="google_realtime",
        voice="Kore",
        language="hi",
    )

    assert result == {"url": "https://signed.example/preview", "cached": True}
    storage.aget_file_metadata.assert_awaited_once_with(
        "voice-previews/google_realtime/gemini-3.1-flash-live-preview/Kore/hi.wav"
    )
    storage.acreate_file.assert_not_awaited()
    assert FakeAsyncClient.calls == []


async def test_google_miss_generates_wav_and_stores_it(patch_env):
    storage = FakeStorage(existing_metadata=None)
    google_response = FakeResponse(
        json_data={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "data": base64.b64encode(PCM_SAMPLE).decode()
                                }
                            }
                        ]
                    }
                }
            ]
        }
    )
    patch_env(
        effective=_realtime_effective(), storage=storage, response=google_response
    )

    result = await voice_preview.get_realtime_voice_preview(
        user_id=1,
        organization_id=1,
        provider="google_realtime",
        voice="Aoede",
        language="hi",
    )

    assert result == {"url": "https://signed.example/preview", "cached": False}

    # Request payload: Gemini TTS endpoint, org API key, Hindi preview text,
    # requested voice name.
    assert len(FakeAsyncClient.calls) == 1
    call = FakeAsyncClient.calls[0]
    assert call["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash-preview-tts:generateContent"
    )
    assert call["headers"] == {"x-goog-api-key": "google-key"}
    payload = call["json"]
    assert payload["contents"] == [
        {"parts": [{"text": voice_preview.PREVIEW_TEXTS["hi"]}]}
    ]
    assert payload["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert (
        payload["generationConfig"]["speechConfig"]["voiceConfig"][
            "prebuiltVoiceConfig"
        ]["voiceName"]
        == "Aoede"
    )

    # Stored WAV: correct cache key, RIFF/WAVE header, PCM wrapped losslessly.
    storage.acreate_file.assert_awaited_once()
    key, content = storage.acreate_file.await_args.args
    assert key == (
        "voice-previews/google_realtime/gemini-3.1-flash-live-preview/Aoede/hi.wav"
    )
    wav_bytes = content.read()
    assert wav_bytes.startswith(b"RIFF")
    assert wav_bytes[8:12] == b"WAVE"
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 24000
        assert wav_file.readframes(wav_file.getnframes()) == PCM_SAMPLE


async def test_unknown_language_falls_back_to_english(patch_env):
    storage = FakeStorage(existing_metadata=None)
    google_response = FakeResponse(
        json_data={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "data": base64.b64encode(PCM_SAMPLE).decode()
                                }
                            }
                        ]
                    }
                }
            ]
        }
    )
    patch_env(
        effective=_realtime_effective(), storage=storage, response=google_response
    )

    await voice_preview.get_realtime_voice_preview(
        user_id=1,
        organization_id=1,
        provider="google_realtime",
        voice="Kore",
        language="xx",
    )

    payload = FakeAsyncClient.calls[0]["json"]
    assert payload["contents"] == [
        {"parts": [{"text": voice_preview.PREVIEW_TEXTS["en"]}]}
    ]


async def test_provider_mismatch_raises_422(patch_env):
    patch_env(effective=_realtime_effective(), storage=FakeStorage())

    with pytest.raises(HTTPException) as exc_info:
        await voice_preview.get_realtime_voice_preview(
            user_id=1,
            organization_id=1,
            provider="openai_realtime",
            voice="alloy",
            language="en",
        )

    assert exc_info.value.status_code == 422


async def test_non_realtime_configuration_raises_422(patch_env):
    effective = EffectiveAIModelConfiguration(
        llm=OpenAILLMService(api_key="llm-key", model="gpt-4.1"),
        is_realtime=False,
    )
    patch_env(effective=effective, storage=FakeStorage())

    with pytest.raises(HTTPException) as exc_info:
        await voice_preview.get_realtime_voice_preview(
            user_id=1,
            organization_id=1,
            provider="google_realtime",
            voice="Kore",
            language="hi",
        )

    assert exc_info.value.status_code == 422


async def test_unsupported_provider_raises_422(patch_env):
    grok = GrokRealtimeLLMConfiguration(api_key="grok-key", voice="Ara")
    storage = FakeStorage(existing_metadata=None)
    patch_env(effective=_realtime_effective(realtime=grok), storage=storage)

    with pytest.raises(HTTPException) as exc_info:
        await voice_preview.get_realtime_voice_preview(
            user_id=1,
            organization_id=1,
            provider="grok_realtime",
            voice="Ara",
            language="en",
        )

    assert exc_info.value.status_code == 422
    assert "not available for grok_realtime" in exc_info.value.detail
    storage.acreate_file.assert_not_awaited()


async def test_upstream_error_maps_to_502(patch_env):
    storage = FakeStorage(existing_metadata=None)
    patch_env(
        effective=_realtime_effective(),
        storage=storage,
        response=FakeResponse(status_code=500),
    )

    with pytest.raises(HTTPException) as exc_info:
        await voice_preview.get_realtime_voice_preview(
            user_id=1,
            organization_id=1,
            provider="google_realtime",
            voice="Kore",
            language="hi",
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Preview generation failed"
    storage.acreate_file.assert_not_awaited()


async def test_openai_preview_posts_speech_request(patch_env):
    from api.services.configuration.registry import OpenAIRealtimeLLMConfiguration

    openai_realtime = OpenAIRealtimeLLMConfiguration(api_key="oai-key", voice="alloy")
    storage = FakeStorage(existing_metadata=None)
    wav_content = voice_preview.pcm_s16le_to_wav(PCM_SAMPLE)
    patch_env(
        effective=_realtime_effective(realtime=openai_realtime),
        storage=storage,
        response=FakeResponse(content=wav_content),
    )

    result = await voice_preview.get_realtime_voice_preview(
        user_id=1,
        organization_id=1,
        provider="openai_realtime",
        voice="verse",
        language="en",
    )

    assert result["cached"] is False
    call = FakeAsyncClient.calls[0]
    assert call["url"] == "https://api.openai.com/v1/audio/speech"
    assert call["headers"] == {"Authorization": "Bearer oai-key"}
    assert call["json"] == {
        "model": "gpt-4o-mini-tts",
        "voice": "verse",
        "input": voice_preview.PREVIEW_TEXTS["en"],
        "response_format": "wav",
    }
    key, content = storage.acreate_file.await_args.args
    assert key == "voice-previews/openai_realtime/gpt-realtime-2/verse/en.wav"
    assert content.read() == wav_content


def test_cache_key_sanitizes_unsafe_characters():
    key = voice_preview.preview_cache_key(
        "google_realtime", "models/gemini live", "Kore!", "hi-IN"
    )

    assert key == "voice-previews/google_realtime/models_gemini_live/Kore_/hi-IN.wav"


async def test_missing_api_key_raises_422(patch_env, monkeypatch):
    monkeypatch.setattr(
        GoogleRealtimeLLMConfiguration, "get_all_api_keys", lambda self: []
    )
    patch_env(effective=_realtime_effective(), storage=FakeStorage())

    with pytest.raises(HTTPException) as exc_info:
        await voice_preview.get_realtime_voice_preview(
            user_id=1,
            organization_id=1,
            provider="google_realtime",
            voice="Kore",
            language="hi",
        )

    assert exc_info.value.status_code == 422
