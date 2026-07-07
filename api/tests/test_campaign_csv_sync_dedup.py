"""CSVSyncService.sync_source_data must not queue the same phone number twice
from a single file, independent of whatever validate_source_data saw."""

import hashlib
from unittest.mock import AsyncMock, patch

import pytest

from api.services.campaign.sources.csv import CSVSyncService


@pytest.mark.asyncio
async def test_sync_source_data_dedupes_duplicate_phone_numbers():
    csv_rows = [
        ["name", "phone_number"],
        ["Alice", "+919876543210"],
        ["Bob", "+919876543211"],
        ["Alice-again", "+919876543210"],
    ]

    service = CSVSyncService()

    with (
        patch.object(
            CSVSyncService, "_fetch_csv_data", new=AsyncMock(return_value=csv_rows)
        ),
        patch("api.services.campaign.sources.csv.db_client") as mock_db,
    ):
        mock_campaign = AsyncMock()
        mock_campaign.source_id = "contacts.csv"
        mock_campaign.orchestrator_metadata = {}
        mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)
        mock_db.get_existing_source_uuids = AsyncMock(return_value=set())
        mock_db.bulk_create_queued_runs = AsyncMock()
        mock_db.update_campaign = AsyncMock()

        total_rows = await service.sync_source_data(campaign_id=1)

        mock_db.bulk_create_queued_runs.assert_awaited_once()
        queued_runs = mock_db.bulk_create_queued_runs.await_args.args[0]
        phone_numbers = [
            run["context_variables"]["phone_number"] for run in queued_runs
        ]
        assert phone_numbers == ["+919876543210", "+919876543211"]
        assert total_rows == 2


@pytest.mark.asyncio
async def test_sync_source_data_dedupes_after_normalization():
    # "9876543210" and "09876543210" are different raw strings but both
    # normalize to "+919876543210" given default_country_code="91" — dedup
    # must run AFTER normalization (matching validate_source_data's order)
    # so these are recognized as the same contact.
    csv_rows = [
        ["name", "phone_number"],
        ["Alice", "9876543210"],
        ["Alice-again", "09876543210"],
    ]

    service = CSVSyncService()

    with (
        patch.object(
            CSVSyncService, "_fetch_csv_data", new=AsyncMock(return_value=csv_rows)
        ),
        patch("api.services.campaign.sources.csv.db_client") as mock_db,
    ):
        mock_campaign = AsyncMock()
        mock_campaign.source_id = "contacts.csv"
        mock_campaign.orchestrator_metadata = {"default_country_code": "91"}
        mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)
        mock_db.get_existing_source_uuids = AsyncMock(return_value=set())
        mock_db.bulk_create_queued_runs = AsyncMock()
        mock_db.update_campaign = AsyncMock()

        total_rows = await service.sync_source_data(campaign_id=1)

        mock_db.bulk_create_queued_runs.assert_awaited_once()
        queued_runs = mock_db.bulk_create_queued_runs.await_args.args[0]
        phone_numbers = [
            run["context_variables"]["phone_number"] for run in queued_runs
        ]
        assert phone_numbers == ["+919876543210"]
        assert total_rows == 1


@pytest.mark.asyncio
async def test_source_uuid_reflects_original_file_position_not_post_dedup_position():
    # Bob-dup (row 2) duplicates Alice (row 1) and gets dropped. Charlie is
    # row 3 in the ORIGINAL file — after dedup he's at list position 2, but
    # his source_uuid must still say row_3, not row_2, so a later sync of an
    # edited file (different dedup outcome) can't shift his uuid and cause
    # him to be re-queued.
    csv_rows = [
        ["name", "phone_number"],
        ["Alice", "+919876543210"],
        ["Bob-dup", "+919876543210"],
        ["Charlie", "+919876543211"],
    ]
    file_hash = hashlib.md5(b"contacts.csv").hexdigest()[:8]

    service = CSVSyncService()

    with (
        patch.object(
            CSVSyncService, "_fetch_csv_data", new=AsyncMock(return_value=csv_rows)
        ),
        patch("api.services.campaign.sources.csv.db_client") as mock_db,
    ):
        mock_campaign = AsyncMock()
        mock_campaign.source_id = "contacts.csv"
        mock_campaign.orchestrator_metadata = {}
        mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)
        mock_db.get_existing_source_uuids = AsyncMock(return_value=set())
        mock_db.bulk_create_queued_runs = AsyncMock()
        mock_db.update_campaign = AsyncMock()

        await service.sync_source_data(campaign_id=1)

        queued_runs = mock_db.bulk_create_queued_runs.await_args.args[0]
        source_uuids = [run["source_uuid"] for run in queued_runs]
        assert source_uuids == [f"csv_{file_hash}_row_1", f"csv_{file_hash}_row_3"]


@pytest.mark.asyncio
async def test_idempotency_skips_already_queued_row_after_dedup():
    # Simulates a crash-retry: Charlie (original row 3) was already queued in
    # a prior attempt at the ORIGINAL-position uuid. A retry that reprocesses
    # the same file (with the same Bob-dup duplicate present) must still skip
    # Charlie, not re-queue him under a shifted uuid.
    csv_rows = [
        ["name", "phone_number"],
        ["Alice", "+919876543210"],
        ["Bob-dup", "+919876543210"],
        ["Charlie", "+919876543211"],
    ]
    file_hash = hashlib.md5(b"contacts.csv").hexdigest()[:8]
    already_queued = {f"csv_{file_hash}_row_3"}

    service = CSVSyncService()

    with (
        patch.object(
            CSVSyncService, "_fetch_csv_data", new=AsyncMock(return_value=csv_rows)
        ),
        patch("api.services.campaign.sources.csv.db_client") as mock_db,
    ):
        mock_campaign = AsyncMock()
        mock_campaign.source_id = "contacts.csv"
        mock_campaign.orchestrator_metadata = {}
        mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)
        mock_db.get_existing_source_uuids = AsyncMock(return_value=already_queued)
        mock_db.bulk_create_queued_runs = AsyncMock()
        mock_db.update_campaign = AsyncMock()

        total_rows = await service.sync_source_data(campaign_id=1)

        mock_db.bulk_create_queued_runs.assert_awaited_once()
        queued_runs = mock_db.bulk_create_queued_runs.await_args.args[0]
        source_uuids = [run["source_uuid"] for run in queued_runs]
        assert source_uuids == [f"csv_{file_hash}_row_1"]
        assert total_rows == 2  # 1 newly queued + 1 already in existing_uuids
