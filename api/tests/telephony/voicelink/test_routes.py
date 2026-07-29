import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from api.services.telephony.providers.voicelink.provider import VoiceLinkProvider
from api.services.telephony.providers.voicelink.routes import handle_voicelink_events


def _provider() -> VoiceLinkProvider:
    return VoiceLinkProvider(
        {
            "api_base": "https://app.voicelink.co.in/api",
            "username": "reseller-user",
            "password": "placeholder-password",
            "did_number": "919484959244",
            "from_numbers": ["919484959244"],
        }
    )


def _body(event: str = "call.completed") -> str:
    return json.dumps(
        {
            "event": event,
            "timestamp": "2026-06-11T10:01:08Z",
            "call": {
                "id": "5b2f9c1e-aaaa-bbbb-cccc-1234567890ab",
                "direction": "outbound",
                "callType": "outbound",
                "from": "919484959244",
                "to": "7340400524",
                "status": "completed",
                "hangupCause": "16",
                "durationSec": 60,
                "customParameters": {"workflow_run_id": 123},
            },
        },
        separators=(",", ":"),
    )


def _request(body: str) -> Request:
    async def receive():
        return {
            "type": "http.request",
            "body": body.encode("utf-8"),
            "more_body": False,
        }

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/telephony/voicelink/events/123",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_voicelink_events_route_processes_status_update():
    provider = _provider()
    body = _body("call.completed")

    with (
        patch(
            "api.services.telephony.providers.voicelink.routes.db_client"
        ) as db_client,
        patch(
            "api.services.telephony.providers.voicelink.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.voicelink.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_voicelink_events(_request(body), workflow_run_id=123)

    assert result == {"status": "success"}
    process_status.assert_awaited_once()
    _, status_update = process_status.await_args.args
    assert status_update.status == "completed"
    assert status_update.call_id == "5b2f9c1e-aaaa-bbbb-cccc-1234567890ab"
    assert status_update.duration == "60"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event,expected_status",
    [
        ("call.initiated", "initiated"),
        ("call.ringing", "ringing"),
        ("call.answered", "in-progress"),
        ("call.ended", "completed"),
        ("call.failed", "failed"),
    ],
)
async def test_voicelink_events_route_maps_lifecycle_events(event, expected_status):
    provider = _provider()

    with (
        patch(
            "api.services.telephony.providers.voicelink.routes.db_client"
        ) as db_client,
        patch(
            "api.services.telephony.providers.voicelink.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.voicelink.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_voicelink_events(
            _request(_body(event)), workflow_run_id=123
        )

    assert result == {"status": "success"}
    _, status_update = process_status.await_args.args
    assert status_update.status == expected_status


@pytest.mark.asyncio
async def test_voicelink_events_route_ignores_unknown_workflow_run():
    with (
        patch(
            "api.services.telephony.providers.voicelink.routes.db_client"
        ) as db_client,
        patch(
            "api.services.telephony.providers.voicelink.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=None)

        result = await handle_voicelink_events(_request(_body()), workflow_run_id=123)

    assert result == {"status": "ignored", "reason": "workflow_run_not_found"}
    process_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_voicelink_events_route_rejects_invalid_json_without_raising():
    with patch(
        "api.services.telephony.providers.voicelink.routes._process_status_update",
        new_callable=AsyncMock,
    ) as process_status:
        result = await handle_voicelink_events(
            _request("not-json{"), workflow_run_id=123
        )

    assert result == {"status": "error", "reason": "invalid_json"}
    process_status.assert_not_awaited()


# ======== INBOUND DID PARSING (routing correctness) ========
#
# VoiceLink is SIP-backed, so the called party in the inbound `start` frame can
# arrive as a bare number, a SIP URI, or user@host. normalize_telephony_address
# passes URI forms through UNCHANGED, so without extract_did the DID lookup
# misses and the inbound call is hung up with "DID not configured".

import pytest

from api.services.telephony.providers.voicelink.routes import (
    _did_digits,
    _read_start_frame,
    extract_did,
)
from api.utils.telephony_address import normalize_telephony_address


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("919484959244", "919484959244"),
        ("+919484959244", "+919484959244"),
        ("sip:919484959244@voicelink.co.in", "919484959244"),
        ("sips:919484959244@voicelink.co.in", "919484959244"),
        ("tel:+919484959244", "+919484959244"),
        ("919484959244@10.0.0.1", "919484959244"),
        ("sip:919484959244@host;user=phone", "919484959244"),
        ("  919484959244  ", "919484959244"),
        ("", ""),
    ],
)
def test_extract_did_strips_scheme_and_host(raw, expected):
    assert extract_did(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "919484959244",
        "+919484959244",
        "sip:919484959244@voicelink.co.in",
        "919484959244@10.0.0.1",
        "tel:+919484959244",
        "91 94849 59244",
    ],
)
def test_sip_forms_normalize_to_the_stored_did(raw):
    """Every realistic spelling must reach the canonical form stored in
    telephony_phone_numbers.address_normalized — otherwise the call is dropped."""
    canonical = normalize_telephony_address(
        extract_did(raw), country_hint="IN"
    ).canonical
    assert canonical == "+919484959244"


def test_digit_tail_ignores_sip_host():
    """Digitizing the whole URI would fold the host into the number and match
    the wrong DID."""
    assert _did_digits("919484959244@10.0.0.1") == "9484959244"
    assert _did_digits("sip:919484959244@voicelink.co.in") == "9484959244"


def test_digit_tail_rescues_trunk_prefixed_did():
    """'0919484959244' canonicalizes to '+91919484959244' (country code
    doubled) — the digit-tail fallback is what still routes it."""
    assert (
        normalize_telephony_address("0919484959244", country_hint="IN").canonical
        == "+91919484959244"
    )
    assert _did_digits("0919484959244") == _did_digits("+919484959244")


# ======== INBOUND START-FRAME READING ========


class _FakeWS:
    def __init__(self, frames):
        self._frames = list(frames)

    async def receive_text(self):
        if not self._frames:
            raise AssertionError("receive_text called more times than expected")
        return self._frames.pop(0)


@pytest.mark.asyncio
async def test_read_start_frame_skips_connected():
    ws = _FakeWS(
        [
            json.dumps({"event": "connected"}),
            json.dumps({"event": "start", "start": {"to": "919484959244"}}),
        ]
    )
    msg = await _read_start_frame(ws)
    assert msg["event"] == "start"


@pytest.mark.asyncio
async def test_read_start_frame_survives_unexpected_pre_roll():
    """A keepalive or stray frame before `start` must not hang up the call —
    that failure is indistinguishable from 'inbound never connected'."""
    ws = _FakeWS(
        [
            json.dumps({"event": "connected"}),
            json.dumps({"event": "ping"}),
            "not-json-at-all",
            json.dumps({"event": "start", "start": {"to": "919484959244"}}),
        ]
    )
    msg = await _read_start_frame(ws)
    assert msg["event"] == "start"


@pytest.mark.asyncio
async def test_read_start_frame_gives_up_after_max_frames():
    ws = _FakeWS([json.dumps({"event": "media"}) for _ in range(5)])
    assert await _read_start_frame(ws) == {}


def test_inbound_ws_route_registered_with_and_without_trailing_slash():
    """A bot URL saved with a trailing slash would otherwise fail the handshake
    at the router with nothing logged by the app at all."""
    from api.services.telephony.providers.voicelink.routes import router

    ws_paths = {
        r.path for r in router.routes if r.path.rstrip("/").endswith("/ws")
    }
    assert "/ws" in ws_paths
    assert "/ws/" in ws_paths
