"""The keyless managed-voice endpoint: gates on managed-Gemini being active and
validates the voice against the Gemini catalog."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes import organization as org_routes


def _user(org=42):
    return SimpleNamespace(id=7, selected_organization_id=org)


@pytest.mark.asyncio
async def test_managed_voice_saves_valid_gemini_voice():
    with (
        patch.object(org_routes, "_managed_gemini_key_for", new=AsyncMock(return_value="k")),
        patch.object(org_routes, "set_managed_gemini_voice", new=AsyncMock()) as setter,
        patch.object(
            org_routes,
            "_model_configuration_v2_response",
            new=AsyncMock(return_value={"ok": True}),
        ),
    ):
        res = await org_routes.set_model_configuration_managed_voice(
            body=org_routes.SetManagedVoiceRequest(voice="Kore"), user=_user()
        )
    assert res == {"ok": True}
    setter.assert_awaited_once_with(42, "Kore")


@pytest.mark.asyncio
async def test_managed_voice_400_when_not_managed():
    with (
        patch.object(org_routes, "_managed_gemini_key_for", new=AsyncMock(return_value=None)),
        patch.object(org_routes, "set_managed_gemini_voice", new=AsyncMock()) as setter,
    ):
        with pytest.raises(HTTPException) as exc:
            await org_routes.set_model_configuration_managed_voice(
                body=org_routes.SetManagedVoiceRequest(voice="Kore"), user=_user()
            )
    assert exc.value.status_code == 400
    setter.assert_not_awaited()


@pytest.mark.asyncio
async def test_managed_voice_400_for_unknown_voice():
    with (
        patch.object(org_routes, "_managed_gemini_key_for", new=AsyncMock(return_value="k")),
        patch.object(org_routes, "set_managed_gemini_voice", new=AsyncMock()) as setter,
    ):
        with pytest.raises(HTTPException) as exc:
            await org_routes.set_model_configuration_managed_voice(
                body=org_routes.SetManagedVoiceRequest(voice="NotAGeminiVoice"),
                user=_user(),
            )
    assert exc.value.status_code == 400
    setter.assert_not_awaited()
