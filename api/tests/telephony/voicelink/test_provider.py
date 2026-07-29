import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.services.telephony.providers.voicelink.provider import (
    VoiceLinkProvider,
    normalize_customer_number,
)


def _provider(**overrides) -> VoiceLinkProvider:
    config = {
        "api_base": "https://app.voicelink.co.in/api",
        "username": "reseller-user",
        "password": "placeholder-password",
        "bearer_token": None,
        "did_number": "919484959244",
        "from_numbers": ["919484959244"],
    }
    config.update(overrides)
    return VoiceLinkProvider(config)


_ADD_LEAD_SUCCESS = (
    201,
    {
        "status": True,
        "message": "Lead added",
        "data": {
            "outbound_queue_id": 991,
            "bot_id": 17,
            "client_id": 474,
            "carrier_id": 3,
        },
    },
)


# ======== NUMBER NORMALIZATION (the 91-strip) ========


@pytest.mark.parametrize(
    "raw,expected",
    [
        # 12-digit starting "91" → strip country code
        ("917340400524", "7340400524"),
        # 11-digit starting "0" → strip trunk prefix
        ("07340400524", "7340400524"),
        # formatted E.164 with spaces → digits only, then 91-strip
        ("+91 73404 00524", "7340400524"),
        ("+91-73404-00524", "7340400524"),
        # bare 10-digit local number → unchanged
        ("7340400524", "7340400524"),
        # 10-digit number that happens to start with 91 → NOT stripped
        ("9184012929", "9184012929"),
        # empty input → empty output
        ("", ""),
    ],
)
def test_normalize_customer_number(raw, expected):
    assert normalize_customer_number(raw) == expected


# ======== ADD_LEAD REQUEST SHAPE ========


@pytest.mark.asyncio
async def test_initiate_call_sends_bare_local_number_and_registered_did():
    provider = _provider()

    with (
        patch.object(
            provider, "_api_request", new_callable=AsyncMock
        ) as api_request,
        patch(
            "api.services.telephony.providers.voicelink.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://example.test", "wss://example.test"),
        ),
    ):
        api_request.return_value = _ADD_LEAD_SUCCESS

        result = await provider.initiate_call(
            to_number="+91 73404 00524",
            webhook_url="https://example.test/api/v1/telephony/voicelink/events",
            workflow_run_id=123,
            workflow_id=7,
            user_id=11,
        )

    api_request.assert_awaited_once()
    method, path, payload = api_request.await_args.args
    assert method == "POST"
    assert path == "/v1/add_lead"

    # ⚠️ customer_number must be the BARE 10-digit local number
    assert payload["customer_number"] == "7340400524"
    # did_number keeps its registered (91-prefixed) form
    assert payload["did_number"] == "919484959244"
    assert payload["websocket_url"] == (
        "wss://example.test/api/v1/telephony/ws/7/11/123"
    )
    assert payload["webhook_url"] == (
        "https://example.test/api/v1/telephony/voicelink/events/123"
    )
    # custom_parameters is a JSON string
    custom = json.loads(payload["custom_parameters"])
    assert custom == {"workflow_id": 7, "user_id": 11, "workflow_run_id": 123}

    assert result.call_id == "991"
    assert result.status == "queued"
    assert result.caller_number == "919484959244"
    assert result.provider_metadata["outbound_queue_id"] == 991
    assert result.provider_metadata["bot_id"] == 17


@pytest.mark.asyncio
async def test_initiate_call_prefers_explicit_from_number():
    provider = _provider()

    with (
        patch.object(
            provider, "_api_request", new_callable=AsyncMock
        ) as api_request,
        patch(
            "api.services.telephony.providers.voicelink.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://example.test", "wss://example.test"),
        ),
    ):
        api_request.return_value = _ADD_LEAD_SUCCESS

        await provider.initiate_call(
            to_number="7340400524",
            webhook_url="unused",
            workflow_run_id=123,
            from_number="+919876543210",
            workflow_id=7,
            user_id=11,
        )

    _, _, payload = api_request.await_args.args
    # Explicit caller id wins; formatting stripped but 91 prefix kept.
    assert payload["did_number"] == "919876543210"


@pytest.mark.asyncio
async def test_initiate_call_raises_on_provider_error():
    provider = _provider()

    with (
        patch.object(
            provider, "_api_request", new_callable=AsyncMock
        ) as api_request,
        patch(
            "api.services.telephony.providers.voicelink.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://example.test", "wss://example.test"),
        ),
    ):
        api_request.return_value = (422, {"status": False, "message": "bad DID"})

        with pytest.raises(HTTPException) as exc_info:
            await provider.initiate_call(
                to_number="7340400524",
                webhook_url="unused",
                workflow_run_id=123,
                workflow_id=7,
                user_id=11,
            )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_initiate_call_requires_routing_ids():
    provider = _provider()

    with pytest.raises(ValueError):
        await provider.initiate_call(
            to_number="7340400524",
            webhook_url="unused",
            workflow_run_id=123,
        )


# ======== 401 → RE-LOGIN → RETRY ========


@pytest.mark.asyncio
async def test_api_request_relogins_and_retries_once_on_401():
    provider = _provider(bearer_token="stale-token")

    with (
        patch.object(provider, "_send_request", new_callable=AsyncMock) as send,
        patch.object(
            provider, "_login", new_callable=AsyncMock, return_value="fresh-token"
        ) as login,
    ):
        send.side_effect = [(401, {"message": "Unauthenticated"}), _ADD_LEAD_SUCCESS]

        status, data = await provider._api_request("POST", "/v1/add_lead", {})

    assert status == 201
    assert data["status"] is True
    login.assert_awaited_once()
    assert send.await_count == 2
    # Retry carries the fresh token
    assert send.await_args_list[1].args[3] == "fresh-token"


@pytest.mark.asyncio
async def test_api_request_does_not_retry_without_login_credentials():
    """With a static token and NO identity to fall back to (no config
    username/password, no reseller env), a 401 is returned as-is — there is
    nothing to re-authenticate as, so the call must not be retried.

    Contrast with test_api_request_falls_back_to_reseller_when_client_login_rejected:
    once a reseller identity IS available, the same 401 DOES trigger a retry.
    """
    import os as _os

    with patch.dict(
        _os.environ,
        {"VOICELINK_RESELLER_USERNAME": "", "VOICELINK_RESELLER_PASSWORD": ""},
    ):
        provider = _provider(
            username=None, password=None, bearer_token="static-token"
        )

    assert provider._identities == []

    with patch.object(provider, "_send_request", new_callable=AsyncMock) as send:
        send.return_value = (401, {"message": "Unauthenticated"})

        status, _ = await provider._api_request("POST", "/v1/add_lead", {})

    assert status == 401
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_api_request_logs_in_first_when_no_token_cached():
    provider = _provider()  # username/password only, no bearer_token

    async def _login():
        provider._access_token = "first-token"
        return "first-token"

    with (
        patch.object(provider, "_send_request", new_callable=AsyncMock) as send,
        patch.object(
            provider, "_login", new_callable=AsyncMock, side_effect=_login
        ) as login,
    ):
        send.return_value = _ADD_LEAD_SUCCESS

        status, _ = await provider._api_request("POST", "/v1/add_lead", {})

    assert status == 201
    login.assert_awaited_once()
    assert send.await_args.args[3] == "first-token"


# ======== WEBHOOK EVENT PARSING → STATUS MAPPING ========


def _event(event: str, **call_overrides) -> dict:
    call = {
        "id": "5b2f9c1e-aaaa-bbbb-cccc-1234567890ab",
        "direction": "outbound",
        "callType": "outbound",
        "from": "919484959244",
        "to": "7340400524",
        "status": "completed",
        "hangupCause": "16",
        "startedAt": "2026-06-11T10:00:00Z",
        "ringingAt": "2026-06-11T10:00:02Z",
        "answeredAt": "2026-06-11T10:00:08Z",
        "endedAt": "2026-06-11T10:01:08Z",
        "ringDurationSec": 6,
        "durationSec": 60,
        "customParameters": {"workflow_run_id": 123},
    }
    call.update(call_overrides)
    return {"event": event, "timestamp": "2026-06-11T10:01:08Z", "call": call}


@pytest.mark.parametrize(
    "event,expected_status",
    [
        ("call.initiated", "initiated"),
        ("call.ringing", "ringing"),
        ("call.answered", "in-progress"),
        ("call.completed", "completed"),
        ("call.ended", "completed"),
        ("call.failed", "failed"),
    ],
)
def test_parse_status_callback_maps_events(event, expected_status):
    provider = _provider()

    parsed = provider.parse_status_callback(_event(event))

    assert parsed["status"] == expected_status
    assert parsed["call_id"] == "5b2f9c1e-aaaa-bbbb-cccc-1234567890ab"
    assert parsed["from_number"] == "919484959244"
    assert parsed["to_number"] == "7340400524"
    assert parsed["direction"] == "outbound"
    assert parsed["duration"] == "60"


def test_parse_status_callback_unknown_event_passes_through():
    provider = _provider()

    parsed = provider.parse_status_callback(_event("call.something_new"))

    assert parsed["status"] == "call.something_new"


@pytest.mark.parametrize("field", ["recordingUrl", "recording_url"])
def test_parse_status_callback_picks_up_recording_url_defensively(field):
    provider = _provider()

    parsed = provider.parse_status_callback(
        _event("call.completed", **{field: "https://cdn.test/rec.mp3"})
    )

    assert parsed["extra"]["recording_url"] == "https://cdn.test/rec.mp3"


def test_parse_status_callback_tolerates_missing_call_object():
    provider = _provider()

    parsed = provider.parse_status_callback({"event": "call.failed"})

    assert parsed["status"] == "failed"
    assert parsed["call_id"] == ""
    assert parsed["duration"] is None


# ======== CONFIG VALIDATION ========


def test_validate_config_accepts_username_password():
    assert _provider(bearer_token=None).validate_config() is True


def test_validate_config_accepts_bearer_token_only():
    provider = _provider(username=None, password=None, bearer_token="token")
    assert provider.validate_config() is True


def test_validate_config_rejects_missing_auth():
    provider = _provider(username=None, password=None, bearer_token=None)
    assert provider.validate_config() is False


def test_validate_config_rejects_missing_did():
    assert _provider(did_number=None).validate_config() is False


# ======== CREDENTIAL CHAIN ========
#
# /v1/auth/login is RESELLER-scoped — a CLIENT username is answered with
# "Invalid credentials." and can never authenticate. Prod configs nonetheless
# carry per-client logins, so the provider must fall through to the reseller
# identity instead of wedging on the client one. The old code only consulted
# the reseller env when password AND bearer_token were both unset, so a config
# holding a stale client login stayed broken forever — that is the bug that
# silently killed every outbound call.

import os

_RESELLER_ENV = {
    "VOICELINK_RESELLER_USERNAME": "reseller-acct",
    "VOICELINK_RESELLER_PASSWORD": "reseller-secret",
}

_LOGIN_PAGE_HTML = (
    '<!DOCTYPE html><html lang="en"><head>'
    "<title>Voice Link - Connect &amp; Communicate</title></head>"
    '<body class="page-signup"><div wire:name="auth.unified-auth"></div></body></html>'
)


@pytest.mark.parametrize(
    "status,data",
    [
        (401, {"message": "Unauthenticated."}),
        (302, {"raw": ""}),
        (307, {"raw": ""}),
        (200, {"raw": _LOGIN_PAGE_HTML}),
    ],
)
def test_is_auth_failure_detects_rejected_token(status, data):
    assert VoiceLinkProvider._is_auth_failure(status, data) is True


@pytest.mark.parametrize(
    "status,data",
    [
        (200, {"status": True, "data": {"outbound_queue_id": 1}}),
        (201, {"status": True, "data": {}}),
        (422, {"status": False, "message": "The did_number field is required."}),
        (500, {"raw": "<html><body>Server Error</body></html>"}),
    ],
)
def test_is_auth_failure_ignores_non_auth_responses(status, data):
    assert VoiceLinkProvider._is_auth_failure(status, data) is False


def test_client_creds_are_tried_first_then_reseller():
    with patch.dict(os.environ, _RESELLER_ENV):
        p = _provider(username="amit.4", password="client-pass")
    assert [i["label"] for i in p._identities] == ["configured", "reseller-env"]
    assert p._identities[0]["username"] == "amit.4"
    assert p._identities[1]["username"] == "reseller-acct"


def test_config_without_credentials_adopts_reseller_identity():
    with patch.dict(os.environ, _RESELLER_ENV):
        p = _provider(username=None, password=None, bearer_token=None)
    assert p.username == "reseller-acct"
    assert [i["label"] for i in p._identities] == ["configured"]
    assert p.validate_config() is True


def test_reseller_not_duplicated_when_config_already_holds_it():
    with patch.dict(os.environ, _RESELLER_ENV):
        p = _provider(username="reseller-acct", password="reseller-secret")
    assert len(p._identities) == 1


@pytest.mark.asyncio
async def test_api_request_falls_back_to_reseller_when_client_login_rejected():
    """THE regression test: a stale CLIENT login must not wedge the provider."""
    with patch.dict(os.environ, _RESELLER_ENV):
        p = _provider(username="Hardikk.client", password="dead-pass")

    async def fake_login_as(identity):
        # VoiceLink rejects client logins; only the reseller authenticates.
        return "reseller-token" if identity["label"] == "reseller-env" else None

    with (
        patch.object(p, "_login_as", side_effect=fake_login_as),
        patch.object(p, "_send_request", new_callable=AsyncMock) as send,
    ):
        send.return_value = (201, {"status": True, "data": {"outbound_queue_id": 9}})
        status, data = await p._api_request("POST", "/v1/add_lead", {})

    assert status == 201
    assert data["data"]["outbound_queue_id"] == 9
    assert send.await_args.args[3] == "reseller-token"


@pytest.mark.asyncio
async def test_api_request_retries_whole_chain_on_login_page_redirect():
    """A dead STORED TOKEN surfaces as a redirect/login-page, not a 401."""
    with patch.dict(os.environ, _RESELLER_ENV):
        p = _provider(bearer_token="stale-token")

    with (
        patch.object(p, "_login_as", AsyncMock(return_value="fresh-token")),
        patch.object(p, "_send_request", new_callable=AsyncMock) as send,
    ):
        send.side_effect = [
            (200, {"raw": _LOGIN_PAGE_HTML}),  # stale token -> login page
            (201, {"status": True, "data": {}}),  # after re-auth
        ]
        status, _ = await p._api_request("POST", "/v1/add_lead", {})

    assert status == 201
    assert send.await_args_list[0].args[3] == "stale-token"
    assert send.await_args_list[1].args[3] == "fresh-token"


@pytest.mark.asyncio
async def test_api_request_does_not_retry_on_non_auth_error():
    with patch.dict(os.environ, _RESELLER_ENV):
        p = _provider(bearer_token="token")

    with (
        patch.object(p, "_send_request", new_callable=AsyncMock) as send,
        patch.object(p, "_login_as", new_callable=AsyncMock) as login_as,
    ):
        send.return_value = (422, {"status": False, "message": "bad request"})
        status, _ = await p._api_request("POST", "/v1/add_lead", {})

    login_as.assert_not_awaited()
    assert send.await_count == 1
    assert status == 422


@pytest.mark.asyncio
async def test_login_raises_only_after_every_identity_rejected():
    with patch.dict(os.environ, _RESELLER_ENV):
        p = _provider(username="amit.4", password="client-pass")

    with patch.object(p, "_login_as", AsyncMock(return_value=None)) as login_as:
        with pytest.raises(HTTPException) as excinfo:
            await p._login()

    assert login_as.await_count == 2  # both identities were genuinely tried
    assert "reseller-scoped" in str(excinfo.value.detail)


# ======== INBOUND WEBSOCKET BOT SYNC (one URL, many clients) ========


def _patch_endpoints():
    return patch(
        "api.services.telephony.providers.voicelink.provider.get_backend_endpoints",
        new_callable=AsyncMock,
        return_value=("https://api.example.test", "wss://api.example.test"),
    )


def test_inbound_ws_url_carries_no_client_or_did():
    """One URL must serve every client — the DID in the start frame does the
    routing, so adding a client never means adding an endpoint."""
    p = _provider()
    url = p._inbound_ws_url("wss://api.example.test")
    assert url == "wss://api.example.test/api/v1/telephony/ws"
    assert "919484959244" not in url
    assert "client" not in url


@pytest.mark.asyncio
async def test_configure_inbound_creates_bot_with_the_shared_url():
    p = _provider(client_id="474")

    with (
        patch.object(p, "_api_request", new_callable=AsyncMock) as api,
        _patch_endpoints(),
    ):
        api.side_effect = [
            (200, {"status": True, "data": {"data": []}}),
            (201, {"status": True, "data": {"id": 17}}),
        ]
        result = await p.configure_inbound("+919484959244", "https://x.test/hook")

    assert result.ok is True
    method, path, payload = api.await_args_list[1].args
    assert path == "/v1/websocket-bot/create"
    assert payload["websocket_url"] == "wss://api.example.test/api/v1/telephony/ws"
    assert payload["client_id"] == "474"


@pytest.mark.asyncio
async def test_configure_inbound_never_hijacks_another_deployments_bot():
    """Real accounts carry bots for other servers ('Dograh HQ', 'Ria RapidX').
    Updating one of those would break that deployment's inbound."""
    p = _provider(client_id="474")
    listing = (
        200,
        {
            "status": True,
            "data": {
                "data": [
                    {
                        "id": 443,
                        "bot_name": "Dograh HQ",
                        "client_id": 474,
                        "websocket_url": "wss://168-144-154-134.sslip.io/api/v1/telephony/ws",
                    },
                    {
                        "id": 365,
                        "bot_name": "Ria RapidX",
                        "client_id": 474,
                        "websocket_url": "wss://tunnel.trycloudflare.com/ws/voicelink/did-1",
                    },
                ]
            },
        },
    )

    with (
        patch.object(p, "_api_request", new_callable=AsyncMock) as api,
        _patch_endpoints(),
    ):
        api.side_effect = [listing, (201, {"status": True})]
        result = await p.configure_inbound("+919484959244", "https://x.test/hook")

    assert result.ok is True
    # Must CREATE a new bot, not update 443 or 365.
    assert api.await_args_list[1].args[1] == "/v1/websocket-bot/create"


@pytest.mark.asyncio
async def test_configure_inbound_reuses_our_own_bot():
    p = _provider(client_id="474")
    listing = (
        200,
        {
            "status": True,
            "data": {
                "data": [
                    {
                        "id": 500,
                        "bot_name": "auto4you-inbound",
                        "client_id": 474,
                        "websocket_url": "wss://old.example/api/v1/telephony/ws",
                    }
                ]
            },
        },
    )

    with (
        patch.object(p, "_api_request", new_callable=AsyncMock) as api,
        _patch_endpoints(),
    ):
        api.side_effect = [listing, (200, {"status": True})]
        result = await p.configure_inbound("+919484959244", "https://x.test/hook")

    assert result.ok is True
    assert api.await_args_list[1].args[1] == "/v1/websocket-bot/update/500"


@pytest.mark.asyncio
async def test_configure_inbound_ignores_bots_of_other_clients():
    p = _provider(client_id="1730")
    listing = (
        200,
        {
            "status": True,
            "data": {
                "data": [
                    {
                        "id": 500,
                        "bot_name": "auto4you-inbound",
                        "client_id": 474,
                        "websocket_url": "wss://api.example.test/api/v1/telephony/ws",
                    }
                ]
            },
        },
    )

    with (
        patch.object(p, "_api_request", new_callable=AsyncMock) as api,
        _patch_endpoints(),
    ):
        api.side_effect = [listing, (201, {"status": True})]
        result = await p.configure_inbound("+919484959244", "https://x.test/hook")

    assert result.ok is True
    assert api.await_args_list[1].args[1] == "/v1/websocket-bot/create"


@pytest.mark.asyncio
async def test_configure_inbound_reports_auth_failure_without_raising():
    p = _provider()

    with (
        patch.object(p, "_api_request", new_callable=AsyncMock) as api,
        _patch_endpoints(),
    ):
        api.return_value = (401, {"message": "Unauthenticated."})
        result = await p.configure_inbound("+919484959244", "https://x.test/hook")

    assert result.ok is False
    assert "wss://api.example.test/api/v1/telephony/ws" in (result.message or "")


@pytest.mark.asyncio
async def test_configure_inbound_clear_is_a_noop():
    p = _provider()
    with patch.object(p, "_api_request", new_callable=AsyncMock) as api:
        result = await p.configure_inbound("+919484959244", None)
    assert result.ok is True
    api.assert_not_awaited()


def test_config_loader_passes_client_id_and_channel_cap():
    from api.services.telephony.providers.voicelink import _config_loader

    loaded = _config_loader(
        {
            "api_base": "https://app.voicelink.co.in/api",
            "did_number": "919484959244",
            "client_id": "474",
            "max_concurrent_calls": 4,
        }
    )
    assert loaded["client_id"] == "474"
    assert loaded["max_concurrent_calls"] == 4
    p = VoiceLinkProvider(loaded)
    assert p.client_id == "474"
    assert p.max_concurrent_calls == 4
