from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from api.services.configuration.registry import (
    DograhEmbeddingsConfiguration,
    DograhLLMService,
    DograhSTTService,
    DograhTTSService,
    EmbeddingsConfig,
    LLMConfig,
    RealtimeConfig,
    ServiceProviders,
    STTConfig,
    TTSConfig,
)

DOGRAH_SPEED_MIN = 0.5
DOGRAH_SPEED_MAX = 2.0
DOGRAH_SPEED_STEP = 0.1
DOGRAH_SPEED_OPTIONS: tuple[float, ...] = (0.8, 1.0, 1.2)
DOGRAH_DEFAULT_VOICE = "default"
DOGRAH_DEFAULT_LANGUAGE = "multi"


class EffectiveAIModelConfiguration(BaseModel):
    llm: LLMConfig | None = None
    stt: STTConfig | None = None
    tts: TTSConfig | None = None
    embeddings: EmbeddingsConfig | None = None
    realtime: RealtimeConfig | None = None
    is_realtime: bool = False
    managed_service_version: int | None = None
    test_phone_number: str | None = None
    timezone: str | None = None
    last_validated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def strip_incomplete_realtime_when_disabled(cls, data):
        """Skip realtime validation when is_realtime is False and api_key is missing."""
        if isinstance(data, dict) and not data.get("is_realtime", False):
            realtime = data.get("realtime")
            if isinstance(realtime, dict) and not realtime.get("api_key"):
                data.pop("realtime", None)
        return data


class WorkflowModelVoiceOverride(BaseModel):
    """Per-workflow voice pick stored under ``model_voice_override``.

    Layers onto whatever configuration a workflow resolves to (org v2,
    legacy v1, or a full workflow v2 override): realtime configs get
    ``realtime.voice``/``realtime.language``, pipeline configs get
    ``tts.voice``/``stt.language``.
    """

    voice: str = Field(min_length=1)
    language: str | None = None

    @field_validator("voice")
    @classmethod
    def validate_voice(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("voice must not be blank")
        return value.strip()

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class DograhManagedAIModelConfiguration(BaseModel):
    # A list means multiple managed MPS keys (e.g. rotation); downstream
    # registry configs accept str | list[str] natively.
    api_key: str | list[str]
    voice: str = DOGRAH_DEFAULT_VOICE
    speed: float = Field(default=1.0, ge=DOGRAH_SPEED_MIN, le=DOGRAH_SPEED_MAX)
    language: str = DOGRAH_DEFAULT_LANGUAGE

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value):
        if isinstance(value, list):
            if not value:
                raise ValueError("api_key list must not be empty")
            if any(not key.strip() for key in value):
                raise ValueError("api_key list entries must not be blank")
        elif not value.strip():
            raise ValueError("api_key must not be blank")
        return value

    def first_api_key(self) -> str:
        """Return a single key for callers that need a plain string."""
        if isinstance(self.api_key, list):
            return self.api_key[0]
        return self.api_key


class BYOKPipelineAIModelConfiguration(BaseModel):
    llm: LLMConfig
    tts: TTSConfig
    stt: STTConfig
    embeddings: EmbeddingsConfig | None = None

    @model_validator(mode="after")
    def reject_dograh_providers(self):
        _reject_dograh_provider("llm", self.llm)
        _reject_dograh_provider("tts", self.tts)
        _reject_dograh_provider("stt", self.stt)
        _reject_dograh_provider("embeddings", self.embeddings)
        return self


class BYOKRealtimeAIModelConfiguration(BaseModel):
    realtime: RealtimeConfig
    llm: LLMConfig
    embeddings: EmbeddingsConfig | None = None

    @model_validator(mode="after")
    def reject_dograh_providers(self):
        _reject_dograh_provider("llm", self.llm)
        _reject_dograh_provider("embeddings", self.embeddings)
        return self


class BYOKAIModelConfiguration(BaseModel):
    mode: Literal["pipeline", "realtime"]
    pipeline: BYOKPipelineAIModelConfiguration | None = None
    realtime: BYOKRealtimeAIModelConfiguration | None = None

    @model_validator(mode="after")
    def validate_selected_mode(self):
        if self.mode == "pipeline" and self.pipeline is None:
            raise ValueError("byok.pipeline is required when byok.mode is pipeline")
        if self.mode == "realtime" and self.realtime is None:
            raise ValueError("byok.realtime is required when byok.mode is realtime")
        return self


class OrganizationAIModelConfigurationV2(BaseModel):
    version: Literal[2] = 2
    mode: Literal["dograh", "byok"]
    dograh: DograhManagedAIModelConfiguration | None = None
    byok: BYOKAIModelConfiguration | None = None

    @model_validator(mode="after")
    def validate_selected_mode(self):
        if self.mode == "dograh" and self.dograh is None:
            raise ValueError("dograh configuration is required when mode is dograh")
        if self.mode == "byok" and self.byok is None:
            raise ValueError("byok configuration is required when mode is byok")
        return self


class OrganizationAIModelConfigurationResponse(BaseModel):
    configuration: dict | None
    effective_configuration: dict
    source: Literal["organization_v2", "legacy_user_v1", "empty"]
    # Set when a stored v2 row exists but fails validation: the platform is
    # silently running on legacy settings, and the UI should surface that.
    configuration_invalid: bool = False
    configuration_error: str | None = None


def compile_ai_model_configuration_v2(
    configuration: OrganizationAIModelConfigurationV2,
) -> EffectiveAIModelConfiguration:
    if configuration.mode == "dograh":
        if configuration.dograh is None:
            raise ValueError("dograh configuration is required")
        return _compile_dograh_configuration(configuration.dograh)

    if configuration.byok is None:
        raise ValueError("byok configuration is required")
    if configuration.byok.mode == "pipeline":
        if configuration.byok.pipeline is None:
            raise ValueError("byok.pipeline is required")
        pipeline = configuration.byok.pipeline
        return EffectiveAIModelConfiguration(
            llm=pipeline.llm,
            tts=pipeline.tts,
            stt=pipeline.stt,
            embeddings=pipeline.embeddings,
            is_realtime=False,
        )

    if configuration.byok.realtime is None:
        raise ValueError("byok.realtime is required")
    realtime = configuration.byok.realtime
    return EffectiveAIModelConfiguration(
        llm=realtime.llm,
        realtime=realtime.realtime,
        embeddings=realtime.embeddings,
        is_realtime=True,
    )


def _compile_dograh_configuration(
    configuration: DograhManagedAIModelConfiguration,
) -> EffectiveAIModelConfiguration:
    return EffectiveAIModelConfiguration(
        llm=DograhLLMService(
            provider=ServiceProviders.DOGRAH,
            api_key=configuration.api_key,
            model="default",
        ),
        tts=DograhTTSService(
            provider=ServiceProviders.DOGRAH,
            api_key=configuration.api_key,
            model="default",
            voice=configuration.voice,
            speed=configuration.speed,
        ),
        stt=DograhSTTService(
            provider=ServiceProviders.DOGRAH,
            api_key=configuration.api_key,
            model="default",
            language=configuration.language,
        ),
        embeddings=DograhEmbeddingsConfiguration(
            provider=ServiceProviders.DOGRAH,
            api_key=configuration.api_key,
            model="dograh_embedding_v1",
        ),
        is_realtime=False,
        managed_service_version=2,
    )


def _reject_dograh_provider(section: str, service) -> None:
    if service is None:
        return
    if getattr(service, "provider", None) == ServiceProviders.DOGRAH:
        raise ValueError(f"BYOK {section} cannot use Dograh provider")
