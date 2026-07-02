"""Telephony marketplace service: available filtering, assign, org-number
lookup (live + local fallback), and local purchase bookkeeping
(persist_org_did / record_number_purchase)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.exc import IntegrityError

from api.services import telephony_marketplace as mkt

DEFAULT_API_BASE = "https://app.voicelink.co.in/api"


def _fake_client(available=None, clients=None, configured=True):
    c = AsyncMock()
    c.is_configured = configured
    c.available_dids = AsyncMock(return_value=available or [])
    c.list_clients = AsyncMock(return_value=clients or [])
    c.map_did = AsyncMock(return_value={"status": True})
    return c


def _patch(fc):
    return patch.object(mkt, "get_voicelink_clients_client", return_value=fc)


async def test_list_available_keeps_only_status_1():
    fc = _fake_client(
        available=[
            {"did_id": 1, "did_number": "9111", "user_status": 1},
            {"did_id": 2, "did_number": "9222", "user_status": 2},  # Assigned
        ]
    )
    with _patch(fc):
        nums = await mkt.list_available_numbers()
    assert [n["did_id"] for n in nums] == [1]


async def test_list_available_empty_when_unconfigured():
    with _patch(_fake_client(configured=False)):
        assert await mkt.list_available_numbers() == []


async def test_assign_number_calls_map_did():
    fc = _fake_client()
    with _patch(fc):
        await mkt.assign_number("474", 942)
    fc.map_did.assert_awaited_once()
    payload = fc.map_did.await_args.args[0]
    assert payload["client_id"] == "474"
    assert payload["did_id"] == 942
    assert payload["user_status"] == 2


async def test_list_org_numbers_filters_to_client():
    fc = _fake_client(
        clients=[
            {"id": 474, "dids": [{"did_id": 942, "did_number": "9484959244"}]},
            {"id": 1333, "dids": []},
        ]
    )
    with _patch(fc):
        nums = await mkt.list_org_numbers("474")
    assert [n["did_id"] for n in nums] == [942]


async def test_list_org_numbers_none_client():
    with _patch(_fake_client()):
        assert await mkt.list_org_numbers(None) == []


# ======== list_org_numbers_resolved (live + local fallback) ========


def _db(**overrides):
    db = MagicMock()
    defaults = {
        "list_telephony_configurations_by_provider": AsyncMock(return_value=[]),
        "list_phone_numbers_for_config": AsyncMock(return_value=[]),
        "update_telephony_configuration": AsyncMock(),
        "set_default_telephony_configuration": AsyncMock(),
        "create_telephony_configuration": AsyncMock(),
        "create_phone_number": AsyncMock(),
        "mark_organization_did_purchased": AsyncMock(return_value=True),
    }
    defaults.update(overrides)
    for name, value in defaults.items():
        setattr(db, name, value)
    return db


async def test_resolved_prefers_live_numbers():
    fc = _fake_client(
        clients=[{"id": 474, "dids": [{"did_id": 942, "did_number": "9484959244"}]}]
    )
    with (
        _patch(fc),
        patch.object(
            mkt,
            "resolve_org_voicelink_client_id",
            new=AsyncMock(return_value=("474", True)),
        ),
        patch.object(mkt, "db_client", new=_db()) as db,
    ):
        nums = await mkt.list_org_numbers_resolved(4)
    assert [n["did_id"] for n in nums] == [942]
    db.list_telephony_configurations_by_provider.assert_not_awaited()


async def test_resolved_falls_back_to_local_truth_and_dedupes():
    config = SimpleNamespace(
        id=8,
        is_default_outbound=True,
        credentials={"did_number": "919484959244"},
    )
    rows = [SimpleNamespace(address="+919484959244")]
    db = _db(
        list_telephony_configurations_by_provider=AsyncMock(return_value=[config]),
        list_phone_numbers_for_config=AsyncMock(return_value=rows),
    )
    with (
        _patch(_fake_client(clients=[])),  # live list knows nothing
        patch.object(
            mkt,
            "resolve_org_voicelink_client_id",
            new=AsyncMock(return_value=("474", True)),
        ),
        patch.object(mkt, "db_client", new=db),
    ):
        nums = await mkt.list_org_numbers_resolved(4)
    # Phone row and credentials carry the same DID -> one deduped local entry.
    assert nums == [{"did_number": "919484959244", "source": "local"}]
    db.list_telephony_configurations_by_provider.assert_awaited_once_with(
        4, "voicelink"
    )


async def test_resolved_empty_when_no_client_and_no_local_records():
    with (
        _patch(_fake_client()),
        patch.object(
            mkt,
            "resolve_org_voicelink_client_id",
            new=AsyncMock(return_value=(None, False)),
        ),
        patch.object(mkt, "db_client", new=_db()),
    ):
        assert await mkt.list_org_numbers_resolved(4) == []


# ======== persist_org_did / record_number_purchase ========


async def test_persist_creates_default_config_and_phone_row():
    db = _db(
        create_telephony_configuration=AsyncMock(return_value=SimpleNamespace(id=77))
    )
    with patch.object(mkt, "db_client", new=db):
        configuration_id, created = await mkt.persist_org_did(
            5, "919484959244", client_id="474", username="jane.5"
        )

    assert (configuration_id, created) == (77, True)
    create_kwargs = db.create_telephony_configuration.await_args.kwargs
    assert create_kwargs["organization_id"] == 5
    assert create_kwargs["provider"] == "voicelink"
    assert create_kwargs["is_default_outbound"] is True
    assert create_kwargs["credentials"] == {
        "api_base": DEFAULT_API_BASE,
        "did_number": "919484959244",
        "username": "jane.5",
        "client_id": "474",
    }
    phone_kwargs = db.create_phone_number.await_args.kwargs
    assert phone_kwargs["organization_id"] == 5
    assert phone_kwargs["telephony_configuration_id"] == 77
    assert phone_kwargs["address"] == "+919484959244"  # bare digits get E.164 "+"


async def test_persist_updates_existing_config_preserving_credentials():
    existing = SimpleNamespace(
        id=42,
        is_default_outbound=False,
        credentials={"api_base": "https://custom.example/api", "username": "u"},
    )
    db = _db(
        list_telephony_configurations_by_provider=AsyncMock(return_value=[existing]),
        update_telephony_configuration=AsyncMock(
            return_value=SimpleNamespace(id=42, is_default_outbound=False)
        ),
    )
    with patch.object(mkt, "db_client", new=db):
        configuration_id, created = await mkt.persist_org_did(
            5, "919484959244", client_id="888"
        )

    assert (configuration_id, created) == (42, False)
    update_call = db.update_telephony_configuration.await_args
    assert update_call.args == (42, 5)
    credentials = update_call.kwargs["credentials"]
    assert credentials["username"] == "u"  # existing auth preserved
    assert credentials["api_base"] == "https://custom.example/api"
    assert credentials["did_number"] == "919484959244"
    assert credentials["client_id"] == "888"
    db.set_default_telephony_configuration.assert_awaited_once_with(42, 5)
    db.create_telephony_configuration.assert_not_awaited()


async def test_persist_swallows_duplicate_phone_row():
    db = _db(
        create_telephony_configuration=AsyncMock(return_value=SimpleNamespace(id=77)),
        create_phone_number=AsyncMock(
            side_effect=IntegrityError("stmt", {}, Exception("duplicate"))
        ),
    )
    with patch.object(mkt, "db_client", new=db):
        configuration_id, created = await mkt.persist_org_did(5, "919484959244")
    assert (configuration_id, created) == (77, True)  # idempotent — no raise


async def test_record_number_purchase_persists_then_arms_kyc_gate():
    with (
        patch.object(
            mkt, "persist_org_did", new=AsyncMock(return_value=(77, True))
        ) as persist,
        patch.object(mkt, "db_client", new=_db()) as db,
    ):
        await mkt.record_number_purchase(
            5, client_id="474", did_number="919484959244", username="jane.5"
        )
    persist.assert_awaited_once_with(
        5, "919484959244", client_id="474", username="jane.5"
    )
    db.mark_organization_did_purchased.assert_awaited_once_with(5)
