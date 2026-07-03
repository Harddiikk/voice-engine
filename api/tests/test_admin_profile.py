"""Tests for the SaaS admin per-client profile / detail / billing endpoints.

Same conventions as test_admin_clients_credits: a minimal FastAPI app with the
router(s) mounted, ``get_superuser`` overridden for happy paths, and the DB
layer + service helpers patched at the route module's own attributes.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.admin_clients import audit_router, router
from api.services.auth.depends import get_superuser


def _superuser():
    return SimpleNamespace(id=1, is_superuser=True, selected_organization_id=99)


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.include_router(audit_router)
    app.dependency_overrides[get_superuser] = _superuser
    return app


def _org(**overrides):
    defaults = {
        "id": 5,
        "provider_id": "org_oss_abc",
        "created_at": None,
        "voicelink_status": "provisioned",
        "voicelink_client_id": "474",
        "voicelink_username": "jane.5",
        "voicelink_error": None,
        "free_call_seconds_remaining": 120,
        "users": [
            SimpleNamespace(id=9, provider_id="oss_abc", email="jane@example.test")
        ],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_PRICING = {
    "per_minute_inr": 6.0,
    "number_price_inr": 500,
    "setup_fee_inr": 300,
    "custom": {
        "per_minute_inr": True,
        "number_price_inr": False,
        "setup_fee_inr": True,
    },
}

_MONEY = {
    "balance_seconds": 120,
    "unlimited": False,
    "per_minute_inr": 6.0,
    "money_left_inr": 12.0,
    "spent_seconds": 60,
    "money_spent_inr": 6.0,
}


# ======== AUTHZ ========


def test_new_routes_return_403_for_non_superuser():
    app = FastAPI()
    app.include_router(router)  # no overrides — real get_superuser runs
    app.include_router(audit_router)
    client = TestClient(app)

    non_superuser = SimpleNamespace(id=2, is_superuser=False)
    with patch(
        "api.services.auth.depends.get_user",
        new=AsyncMock(return_value=non_superuser),
    ):
        results = [
            client.get("/admin/clients/5"),
            client.patch("/admin/clients/5/profile", json={"suspended": True}),
            client.post("/admin/clients/5/notes", json={"text": "hi"}),
            client.post("/admin/clients/5/charge-setup-fee", json={}),
            client.post("/admin/clients", json={"email": "a@b.test"}),
            client.get("/admin/audit"),
        ]

    assert [r.status_code for r in results] == [403] * 6


# ======== PATCH PROFILE ========


def test_patch_profile_forwards_only_sent_fields():
    app = _make_test_app()
    client = TestClient(app)

    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.update_admin_profile", new=AsyncMock()
        ) as update,
        patch("api.routes.admin_clients.record_admin_action", new=AsyncMock()),
        patch(
            "api.routes.admin_clients.get_admin_profile",
            new=AsyncMock(return_value={"suspended": True}),
        ),
        patch(
            "api.routes.admin_clients.get_org_plan",
            new=AsyncMock(return_value="growth"),
        ),
        patch(
            "api.routes.admin_clients.get_org_pricing",
            new=AsyncMock(return_value=_PRICING),
        ),
    ):
        db.get_organization_by_id = AsyncMock(return_value=_org())

        response = client.patch(
            "/admin/clients/5/profile",
            json={"per_minute_inr": 7.5, "suspended": True},
        )

    assert response.status_code == 200
    # Only the two sent fields are forwarded — plan_override / number_price_inr /
    # setup_fee_inr are omitted (kept unchanged, not cleared).
    update.assert_awaited_once_with(5, per_minute_inr=7.5, suspended=True)
    body = response.json()
    assert body["organization_id"] == 5
    assert body["plan"] == "growth"
    assert body["features"] == {"api": True, "mcp": False, "build_with_ai": True}
    assert body["pricing"]["setup_fee_inr"] == 300


def test_patch_profile_sent_null_clears_override():
    """A field sent as null must be forwarded (to clear), not omitted."""
    app = _make_test_app()
    client = TestClient(app)

    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.update_admin_profile", new=AsyncMock()
        ) as update,
        patch("api.routes.admin_clients.record_admin_action", new=AsyncMock()),
        patch(
            "api.routes.admin_clients.get_admin_profile",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "api.routes.admin_clients.get_org_plan",
            new=AsyncMock(return_value="trial"),
        ),
        patch(
            "api.routes.admin_clients.get_org_pricing",
            new=AsyncMock(return_value=_PRICING),
        ),
    ):
        db.get_organization_by_id = AsyncMock(return_value=_org())

        response = client.patch(
            "/admin/clients/5/profile", json={"plan_override": None}
        )

    assert response.status_code == 200
    update.assert_awaited_once_with(5, plan_override=None)


def test_patch_profile_rejects_bad_plan():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.admin_clients.db_client") as db:
        db.get_organization_by_id = AsyncMock(return_value=_org())
        response = client.patch(
            "/admin/clients/5/profile", json={"plan_override": "platinum"}
        )
    assert response.status_code == 422


def test_patch_profile_404_for_unknown_org():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.admin_clients.db_client") as db:
        db.get_organization_by_id = AsyncMock(return_value=None)
        response = client.patch(
            "/admin/clients/999/profile", json={"suspended": True}
        )
    assert response.status_code == 404


# ======== NOTES ========


def test_add_note_appends_and_returns_notes():
    app = _make_test_app()
    client = TestClient(app)

    notes = [{"at": "2026-07-03T00:00:00+00:00", "by": 1, "text": "called client"}]
    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.append_note",
            new=AsyncMock(return_value={"notes": notes}),
        ) as append,
        patch("api.routes.admin_clients.record_admin_action", new=AsyncMock()),
    ):
        db.get_organization_by_id = AsyncMock(return_value=_org())
        response = client.post(
            "/admin/clients/5/notes", json={"text": "called client"}
        )

    assert response.status_code == 200
    append.assert_awaited_once_with(5, by_user_id=1, text="called client")
    assert response.json()["notes"] == notes


def test_add_note_rejects_empty_text():
    app = _make_test_app()
    client = TestClient(app)
    with patch("api.routes.admin_clients.db_client") as db:
        db.get_organization_by_id = AsyncMock(return_value=_org())
        response = client.post("/admin/clients/5/notes", json={"text": ""})
    assert response.status_code == 422


# ======== CHARGE SETUP FEE ========


def test_charge_setup_fee_charges_configured_fee():
    app = _make_test_app()
    client = TestClient(app)

    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.get_org_pricing",
            new=AsyncMock(return_value=_PRICING),
        ),
        patch(
            "api.routes.admin_clients.get_org_money",
            new=AsyncMock(return_value=_MONEY),
        ),
        patch("api.routes.admin_clients.record_admin_action", new=AsyncMock()),
    ):
        db.get_organization_by_id = AsyncMock(return_value=_org())
        db.charge_purchase_tx = AsyncMock(return_value=5000)

        response = client.post("/admin/clients/5/charge-setup-fee", json={})

    assert response.status_code == 200
    # ₹300 at ₹6/min = 3000 credit-seconds.
    db.charge_purchase_tx.assert_awaited_once_with(
        5, 3000, kind="setup_fee", description="Setup fee — ₹300"
    )
    body = response.json()
    assert body["fee_inr"] == 300
    assert body["charged_seconds"] == 3000
    assert body["credits_seconds_remaining"] == 5000
    assert body["money"]["money_left_inr"] == 12.0


def test_charge_setup_fee_amount_override():
    app = _make_test_app()
    client = TestClient(app)

    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.get_org_pricing",
            new=AsyncMock(return_value=_PRICING),
        ),
        patch(
            "api.routes.admin_clients.get_org_money",
            new=AsyncMock(return_value=_MONEY),
        ),
        patch("api.routes.admin_clients.record_admin_action", new=AsyncMock()),
    ):
        db.get_organization_by_id = AsyncMock(return_value=_org())
        db.charge_purchase_tx = AsyncMock(return_value=100)

        response = client.post(
            "/admin/clients/5/charge-setup-fee", json={"amount_inr": 600}
        )

    assert response.status_code == 200
    # ₹600 at ₹6/min = 6000 credit-seconds.
    db.charge_purchase_tx.assert_awaited_once_with(
        5, 6000, kind="setup_fee", description="Setup fee — ₹600"
    )


def test_charge_setup_fee_400_when_no_fee():
    app = _make_test_app()
    client = TestClient(app)

    zero_fee = dict(_PRICING, setup_fee_inr=0)
    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.get_org_pricing",
            new=AsyncMock(return_value=zero_fee),
        ),
    ):
        db.get_organization_by_id = AsyncMock(return_value=_org())
        db.charge_purchase_tx = AsyncMock()

        response = client.post("/admin/clients/5/charge-setup-fee", json={})

    assert response.status_code == 400
    db.charge_purchase_tx.assert_not_awaited()


def test_charge_setup_fee_409_for_unmetered():
    app = _make_test_app()
    client = TestClient(app)

    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.get_org_pricing",
            new=AsyncMock(return_value=_PRICING),
        ),
    ):
        db.get_organization_by_id = AsyncMock(return_value=_org())
        db.charge_purchase_tx = AsyncMock(return_value="unmetered")

        response = client.post("/admin/clients/5/charge-setup-fee", json={})

    assert response.status_code == 409


def test_charge_setup_fee_402_when_insufficient():
    app = _make_test_app()
    client = TestClient(app)

    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.get_org_pricing",
            new=AsyncMock(return_value=_PRICING),
        ),
    ):
        db.get_organization_by_id = AsyncMock(return_value=_org())
        db.charge_purchase_tx = AsyncMock(return_value=None)

        response = client.post("/admin/clients/5/charge-setup-fee", json={})

    assert response.status_code == 402


# ======== DETAIL ========


def test_client_detail_assembles_all_sections():
    app = _make_test_app()
    client = TestClient(app)

    profile = {
        "plan_override": "growth",
        "suspended": False,
        "notes": [{"at": "2026-07-03T00:00:00+00:00", "by": 1, "text": "n"}],
    }
    overview = {
        "period": "month",
        "totals": {
            "total_calls": 42,
            "total_minutes": 12.5,
            "connected_calls": 30,
        },
    }
    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.get_admin_profile",
            new=AsyncMock(return_value=profile),
        ),
        patch(
            "api.routes.admin_clients.get_org_plan",
            new=AsyncMock(return_value="growth"),
        ),
        patch(
            "api.routes.admin_clients.get_org_pricing",
            new=AsyncMock(return_value=_PRICING),
        ),
        patch(
            "api.routes.admin_clients.get_org_money",
            new=AsyncMock(return_value=_MONEY),
        ),
        patch(
            "api.routes.admin_clients.get_kyc_client",
            return_value=SimpleNamespace(is_configured=False),
        ),
    ):
        db.get_organization_with_users = AsyncMock(return_value=_org())
        db.list_telephony_configurations_by_provider = AsyncMock(return_value=[])
        db.get_organization_overview = AsyncMock(return_value=overview)

        response = client.get("/admin/clients/5")

    assert response.status_code == 200
    body = response.json()
    assert body["organization_id"] == 5
    assert body["owner_email"] == "jane@example.test"
    assert body["plan"] == "growth"
    assert body["plan_override"] == "growth"
    assert body["features"] == {"api": True, "mcp": False, "build_with_ai": True}
    assert body["pricing"]["custom"]["per_minute_inr"] is True
    assert body["money"]["money_left_inr"] == 12.0
    assert body["kyc"]["status"] == "disabled"
    assert body["usage"]["total_calls"] == 42
    assert body["usage"]["money_spent_inr"] == 6.0
    assert len(body["notes"]) == 1


def test_client_detail_404_for_unknown_org():
    app = _make_test_app()
    client = TestClient(app)
    with patch("api.routes.admin_clients.db_client") as db:
        db.get_organization_with_users = AsyncMock(return_value=None)
        response = client.get("/admin/clients/999")
    assert response.status_code == 404


# ======== CREATE CLIENT ========


def test_create_client_creates_user_org_plan_and_credits():
    app = _make_test_app()
    client = TestClient(app)

    new_user = SimpleNamespace(
        id=42, provider_id="oss_new", email="new@client.test"
    )
    new_org = SimpleNamespace(
        id=77, provider_id="org_oss_new", free_call_seconds_remaining=0
    )
    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.generate_client_password",
            return_value="Gen3ratedPassw0rd!!",
        ),
        patch("api.routes.admin_clients.hash_password", return_value="hashed"),
        patch(
            "api.routes.admin_clients.create_user_configuration_with_mps_key",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "api.routes.admin_clients.stash_voicelink_signup_secret",
            new=AsyncMock(),
        ),
        patch(
            "api.routes.admin_clients.update_admin_profile", new=AsyncMock()
        ) as update,
        patch(
            "api.routes.admin_clients.get_org_plan",
            new=AsyncMock(return_value="starter"),
        ),
        patch("api.routes.admin_clients.record_admin_action", new=AsyncMock()),
    ):
        db.get_user_by_email = AsyncMock(return_value=None)
        db.create_user_with_email = AsyncMock(return_value=new_user)
        db.get_or_create_organization_by_provider_id = AsyncMock(
            return_value=(new_org, True)
        )
        db.add_user_to_organization = AsyncMock()
        db.update_user_selected_organization = AsyncMock()
        db.grant_credits_tx = AsyncMock(return_value=600)

        response = client.post(
            "/admin/clients",
            json={
                "email": "New@Client.test",
                "name": "New Co",
                "plan": "starter",
                "initial_credit_minutes": 10,
            },
        )

    assert response.status_code == 200
    db.create_user_with_email.assert_awaited_once_with(
        email="new@client.test", password_hash="hashed", name="New Co"
    )
    db.get_or_create_organization_by_provider_id.assert_awaited_once_with(
        org_provider_id="org_oss_new", user_id=42
    )
    update.assert_awaited_once_with(77, plan_override="starter")
    db.grant_credits_tx.assert_awaited_once_with(
        77, 600, created_by=1, description="Admin initial grant: 10 minutes"
    )
    body = response.json()
    assert body["organization_id"] == 77
    assert body["owner_user_id"] == 42
    assert body["plan"] == "starter"
    assert body["credits_seconds_remaining"] == 600
    assert body["temporary_password"] == "Gen3ratedPassw0rd!!"


def test_create_client_409_for_existing_email():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.admin_clients.db_client") as db:
        db.get_user_by_email = AsyncMock(return_value=_org())  # any truthy user
        response = client.post(
            "/admin/clients", json={"email": "taken@client.test"}
        )

    assert response.status_code == 409


# ======== AUDIT LIST ========


def test_audit_list_maps_rows_and_filters_by_org():
    app = _make_test_app()
    client = TestClient(app)

    rows = [
        SimpleNamespace(
            id=3,
            actor_user_id=1,
            target_organization_id=5,
            action="update_profile",
            detail={"suspended": True},
            created_at=None,
        ),
        SimpleNamespace(
            id=2,
            actor_user_id=1,
            target_organization_id=5,
            action="add_note",
            detail={"text": "n"},
            created_at=None,
        ),
    ]
    with patch("api.routes.admin_clients.db_client") as db:
        db.list_admin_audit = AsyncMock(return_value=rows)
        response = client.get("/admin/audit?org_id=5&limit=10&offset=0")

    assert response.status_code == 200
    db.list_admin_audit.assert_awaited_once_with(
        target_organization_id=5, limit=10, offset=0
    )
    items = response.json()["items"]
    assert [i["id"] for i in items] == [3, 2]
    assert items[0]["action"] == "update_profile"
    assert items[0]["detail"] == {"suspended": True}
