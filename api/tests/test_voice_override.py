"""Pure/mocked tests for the per-workflow model_voice_override layer.

No DB: org/legacy resolution is monkeypatched where a test needs it.
"""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from api.schemas.ai_model_configuration import (
    EffectiveAIModelConfiguration,
    WorkflowModelVoiceOverride,
)
from api.services.configuration import (
    ai_model_configuration as ai_model_configuration_service,
)
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
    WORKFLOW_MODEL_VOICE_OVERRIDE_KEY,
    ResolvedAIModelConfiguration,
    apply_model_voice_override,
    get_effective_ai_model_configuration_for_workflow,
    normalize_workflow_model_voice_override,
)
from api.services.configuration.registry import (
    DeepgramSTTConfiguration,
    ElevenlabsTTSConfiguration,
    GoogleRealtimeLLMConfiguration,
    OpenAILLMService,
    OpenAIRealtimeLLMConfiguration,
)


def _realtime_effective(voice="Kore", language="hi") -> EffectiveAIModelConfiguration:
    return EffectiveAIModelConfiguration(
        llm=OpenAILLMService(api_key="llm-key", model="gpt-4.1"),
        realtime=GoogleRealtimeLLMConfiguration(
            api_key="google-key",
            model="gemini-3.1-flash-live-preview",
            voice=voice,
            language=language,
        ),
        is_realtime=True,
    )


def _pipeline_effective(voice="voice-a", language="en") -> EffectiveAIModelConfiguration:
    return EffectiveAIModelConfiguration(
        llm=OpenAILLMService(api_key="llm-key", model="gpt-4.1"),
        tts=ElevenlabsTTSConfiguration(api_key="tts-key", voice=voice),
        stt=DeepgramSTTConfiguration(api_key="stt-key", language=language),
        is_realtime=False,
    )


# ---------------------------------------------------------------------------
# apply_model_voice_override (pure)
# ---------------------------------------------------------------------------


def test_realtime_override_patches_voice_and_language():
    effective = _realtime_effective(voice="Kore", language="hi")

    patched = apply_model_voice_override(
        effective, {"voice": "Aoede", "language": "en"}
    )

    assert patched.realtime.voice == "Aoede"
    assert patched.realtime.language == "en"
    # Original never mutated.
    assert effective.realtime.voice == "Kore"
    assert effective.realtime.language == "hi"
    # Everything else untouched.
    assert patched.llm.model == "gpt-4.1"
    assert patched.is_realtime is True


def test_realtime_override_voice_only_keeps_language():
    effective = _realtime_effective(voice="Kore", language="hi")

    patched = apply_model_voice_override(effective, {"voice": "Puck"})

    assert patched.realtime.voice == "Puck"
    assert patched.realtime.language == "hi"


def test_realtime_override_skips_language_when_provider_has_no_language_field():
    effective = EffectiveAIModelConfiguration(
        llm=OpenAILLMService(api_key="llm-key", model="gpt-4.1"),
        realtime=OpenAIRealtimeLLMConfiguration(api_key="oai-key", voice="alloy"),
        is_realtime=True,
    )

    patched = apply_model_voice_override(
        effective, {"voice": "verse", "language": "hi"}
    )

    assert patched.realtime.voice == "verse"
    assert "language" not in type(patched.realtime).model_fields
    assert patched.realtime.model_dump().get("language") is None


def test_pipeline_override_patches_tts_voice_and_stt_language():
    effective = _pipeline_effective(voice="voice-a", language="en")

    patched = apply_model_voice_override(
        effective, {"voice": "voice-b", "language": "hi"}
    )

    assert patched.tts.voice == "voice-b"
    assert patched.stt.language == "hi"
    assert effective.tts.voice == "voice-a"
    assert effective.stt.language == "en"


def test_pipeline_override_without_language_keeps_stt():
    effective = _pipeline_effective(voice="voice-a", language="en")

    patched = apply_model_voice_override(effective, {"voice": "voice-b"})

    assert patched.tts.voice == "voice-b"
    assert patched.stt.language == "en"


@pytest.mark.parametrize(
    "override",
    [None, {}, {"voice": ""}, {"voice": "   "}, {"language": "hi"}],
)
def test_falsy_or_blank_override_is_a_noop(override):
    effective = _realtime_effective(voice="Kore", language="hi")

    patched = apply_model_voice_override(effective, override)

    assert patched.realtime.voice == "Kore"
    assert patched.realtime.language == "hi"


def test_override_on_empty_config_is_a_noop():
    effective = EffectiveAIModelConfiguration()

    patched = apply_model_voice_override(effective, {"voice": "Aoede"})

    assert patched.tts is None
    assert patched.realtime is None


# ---------------------------------------------------------------------------
# get_effective_ai_model_configuration_for_workflow branches
# ---------------------------------------------------------------------------


def _mock_resolution(monkeypatch, effective, source="organization_v2"):
    monkeypatch.setattr(
        ai_model_configuration_service,
        "get_resolved_ai_model_configuration",
        AsyncMock(
            return_value=ResolvedAIModelConfiguration(
                effective=effective, source=source
            )
        ),
    )


async def test_realtime_org_config_gets_voice_override(monkeypatch):
    _mock_resolution(monkeypatch, _realtime_effective(voice="Kore", language="hi"))

    effective = await get_effective_ai_model_configuration_for_workflow(
        user_id=1,
        organization_id=1,
        workflow_configurations={
            WORKFLOW_MODEL_VOICE_OVERRIDE_KEY: {"voice": "Charon", "language": "en"}
        },
    )

    assert effective.realtime.voice == "Charon"
    assert effective.realtime.language == "en"


async def test_voice_override_wins_over_full_v2_override():
    # Full workflow v2 override branch returns early and never touches the
    # DB — the voice pick must still layer on top of it.
    v2_override = {
        "version": 2,
        "mode": "byok",
        "byok": {
            "mode": "realtime",
            "realtime": {
                "realtime": {
                    "provider": "google_realtime",
                    "api_key": "admin-key",
                    "model": "gemini-3.1-flash-live-preview",
                    "voice": "Puck",
                    "language": "en",
                },
                "llm": {
                    "provider": "openai",
                    "api_key": "llm-key",
                    "model": "gpt-4.1",
                },
            },
        },
    }

    effective = await get_effective_ai_model_configuration_for_workflow(
        user_id=1,
        organization_id=1,
        workflow_configurations={
            WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY: v2_override,
            WORKFLOW_MODEL_VOICE_OVERRIDE_KEY: {"voice": "Kore", "language": "hi"},
        },
    )

    assert effective.is_realtime is True
    assert effective.realtime.voice == "Kore"
    assert effective.realtime.language == "hi"


async def test_legacy_pipeline_with_model_overrides_gets_voice_override(monkeypatch):
    _mock_resolution(
        monkeypatch,
        _pipeline_effective(voice="voice-a", language="en"),
        source="legacy_user_v1",
    )

    effective = await get_effective_ai_model_configuration_for_workflow(
        user_id=1,
        organization_id=1,
        workflow_configurations={
            "model_overrides": {"tts": {"voice": "voice-b"}},
            WORKFLOW_MODEL_VOICE_OVERRIDE_KEY: {"voice": "voice-c", "language": "hi"},
        },
    )

    # Voice pick layers on AFTER legacy partial overrides.
    assert effective.tts.voice == "voice-c"
    assert effective.stt.language == "hi"


async def test_no_override_leaves_config_unchanged(monkeypatch):
    _mock_resolution(monkeypatch, _realtime_effective(voice="Kore", language="hi"))

    effective = await get_effective_ai_model_configuration_for_workflow(
        user_id=1,
        organization_id=1,
        workflow_configurations={},
    )

    assert effective.realtime.voice == "Kore"
    assert effective.realtime.language == "hi"


# ---------------------------------------------------------------------------
# normalize_workflow_model_voice_override (route-level normalization)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [None, {}, {"voice": ""}, {"voice": "   "}, {"voice": None}],
)
def test_blank_or_empty_override_is_popped(raw):
    configurations = {"max_call_duration": 60, WORKFLOW_MODEL_VOICE_OVERRIDE_KEY: raw}

    normalized = normalize_workflow_model_voice_override(configurations)

    assert WORKFLOW_MODEL_VOICE_OVERRIDE_KEY not in normalized
    assert normalized["max_call_duration"] == 60
    # Input dict not mutated.
    assert WORKFLOW_MODEL_VOICE_OVERRIDE_KEY in configurations


def test_valid_override_is_normalized():
    configurations = {
        WORKFLOW_MODEL_VOICE_OVERRIDE_KEY: {"voice": " Kore ", "language": None}
    }

    normalized = normalize_workflow_model_voice_override(configurations)

    assert normalized[WORKFLOW_MODEL_VOICE_OVERRIDE_KEY] == {"voice": "Kore"}


def test_missing_key_passthrough():
    configurations = {"max_call_duration": 60}

    assert normalize_workflow_model_voice_override(configurations) is configurations


def test_invalid_override_payload_raises():
    with pytest.raises(ValidationError):
        normalize_workflow_model_voice_override(
            {WORKFLOW_MODEL_VOICE_OVERRIDE_KEY: {"voice": "Kore", "language": 42}}
        )


def test_schema_strips_voice_and_language():
    override = WorkflowModelVoiceOverride(voice=" Kore ", language=" hi ")

    assert override.voice == "Kore"
    assert override.language == "hi"
