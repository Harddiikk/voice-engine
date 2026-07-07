"""CSVSyncService.sync_source_data must not queue the same phone number twice
from a single file, independent of whatever validate_source_data saw."""

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
