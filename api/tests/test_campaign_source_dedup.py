"""Tests for CampaignSourceSyncService.dedupe_by_phone_number."""

from api.services.campaign.source_sync import CampaignSourceSyncService


class TestDedupeByPhoneNumber:
    def test_no_duplicates_is_a_noop(self):
        rows = [
            ["Alice", "+919876543210"],
            ["Bob", "+919876543211"],
        ]
        deduped, duplicate_count = CampaignSourceSyncService.dedupe_by_phone_number(
            rows, phone_number_idx=1
        )
        assert deduped == rows
        assert duplicate_count == 0

    def test_keeps_first_occurrence_drops_later_duplicates(self):
        rows = [
            ["Alice-first", "+919876543210"],
            ["Bob", "+919876543211"],
            ["Alice-duplicate", "+919876543210"],
        ]
        deduped, duplicate_count = CampaignSourceSyncService.dedupe_by_phone_number(
            rows, phone_number_idx=1
        )
        assert deduped == [
            ["Alice-first", "+919876543210"],
            ["Bob", "+919876543211"],
        ]
        assert duplicate_count == 1

    def test_empty_phone_rows_pass_through_and_are_not_deduped_against_each_other(self):
        rows = [
            ["Alice", "+919876543210"],
            ["NoPhone1", ""],
            ["NoPhone2", ""],
        ]
        deduped, duplicate_count = CampaignSourceSyncService.dedupe_by_phone_number(
            rows, phone_number_idx=1
        )
        assert deduped == rows
        assert duplicate_count == 0

    def test_row_shorter_than_phone_index_passes_through(self):
        rows = [
            ["Alice", "+919876543210"],
            ["TooShort"],
        ]
        deduped, duplicate_count = CampaignSourceSyncService.dedupe_by_phone_number(
            rows, phone_number_idx=1
        )
        assert deduped == rows
        assert duplicate_count == 0
