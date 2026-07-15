# Campaign Contact Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a campaign contact upload (CSV/Excel) contains duplicate phone numbers, automatically dedupe them (keep the first occurrence) instead of rejecting the whole upload, and show the user how many were removed.

**Architecture:** A shared static helper (`CampaignSourceSyncService.dedupe_by_phone_number`) is called from both `validate_source_data` (synchronous validation at campaign-creation time) and `CSVSyncService.sync_source_data` (the background task that actually creates dialable `queued_runs`), since the two methods independently re-parse the source file. The duplicate count flows from validation through `CampaignResponse` to a frontend toast.

**Tech Stack:** Python/FastAPI backend (`api/services/campaign/`, `api/routes/campaign.py`), pytest, Next.js/TypeScript frontend (`ui/src/app/campaigns/new/page.tsx`), auto-generated API client (`npm run generate-client`).

## Global Constraints

- Keep the **first** occurrence of a duplicate phone number; drop later duplicate rows (confirmed decision).
- Rows with an empty/missing phone number are never counted as duplicates of each other and pass through unchanged (matches existing behavior in `validate_source_data`).
- `CSVSyncService` is the only implementation of `CampaignSourceSyncService` — no other source type needs changes.
- Don't touch `normalize_phone_number` — dedup operates on already-normalized values.

---

### Task 1: Shared dedup helper + `ValidationResult.duplicate_count` field

**Files:**
- Modify: `api/services/campaign/source_sync.py`
- Test: `api/tests/test_campaign_source_dedup.py` (new file)

**Interfaces:**
- Produces: `CampaignSourceSyncService.dedupe_by_phone_number(rows: List[List[str]], phone_number_idx: int) -> tuple[List[List[str]], int]` — a `@staticmethod`. Returns `(deduped_rows, duplicate_count)`. `deduped_rows` preserves original row order for all kept rows.
- Produces: `ValidationResult.duplicate_count: Optional[int] = None` (new field on the existing `@dataclass`).

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_campaign_source_dedup.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_campaign_source_dedup.py -v`
Expected: FAIL with `AttributeError: type object 'CampaignSourceSyncService' has no attribute 'dedupe_by_phone_number'`

- [ ] **Step 3: Add `duplicate_count` to `ValidationResult` and implement the helper**

In `api/services/campaign/source_sync.py`, modify the `ValidationResult` dataclass:

```python
@dataclass
class ValidationResult:
    """Result of source validation."""

    is_valid: bool
    error: Optional[ValidationError] = None
    headers: Optional[List[str]] = field(default=None, repr=False)
    rows: Optional[List[List[str]]] = field(default=None, repr=False)
    # Count of duplicate-phone-number rows removed by dedupe_by_phone_number.
    # None when dedup wasn't run (e.g. validation failed before reaching it).
    duplicate_count: Optional[int] = None
```

Add the helper as a new method on `CampaignSourceSyncService`, right after `normalize_phone_number`:

```python
    @staticmethod
    def dedupe_by_phone_number(
        rows: List[List[str]], phone_number_idx: int
    ) -> tuple[List[List[str]], int]:
        """Keep the first row for each phone number; drop later duplicate rows.

        Rows with no value at ``phone_number_idx`` (empty, or the row is
        shorter than the index) pass through unchanged and are never treated
        as duplicates of one another — matches the existing empty-phone
        handling in validate_source_data / sync_source_data.
        """
        seen_phones: set[str] = set()
        deduped_rows: List[List[str]] = []
        duplicate_count = 0
        for row in rows:
            if len(row) <= phone_number_idx:
                deduped_rows.append(row)
                continue
            phone_number = row[phone_number_idx].strip()
            if not phone_number:
                deduped_rows.append(row)
                continue
            if phone_number in seen_phones:
                duplicate_count += 1
                continue
            seen_phones.add(phone_number)
            deduped_rows.append(row)
        return deduped_rows, duplicate_count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_campaign_source_dedup.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add api/services/campaign/source_sync.py api/tests/test_campaign_source_dedup.py
git commit -m "feat(campaigns): add phone-number dedup helper"
```

---

### Task 2: `validate_source_data` dedupes instead of rejecting

**Files:**
- Modify: `api/services/campaign/source_sync.py:207-235` (the "Check for duplicate phone numbers" block)
- Test: `api/tests/test_campaign_source_dedup.py` (append to existing file)

**Interfaces:**
- Consumes: `CampaignSourceSyncService.dedupe_by_phone_number(rows, phone_number_idx) -> (deduped_rows, duplicate_count)` from Task 1.
- Produces: `validate_source_data(...)` now never fails solely due to duplicate phone numbers; on success, `ValidationResult.rows` is deduped and `ValidationResult.duplicate_count` reflects how many were removed.

- [ ] **Step 1: Write the failing test**

Append to `api/tests/test_campaign_source_dedup.py`:

```python
class TestValidateSourceDataDedup:
    def test_duplicates_are_removed_not_rejected(self):
        headers = ["name", "phone_number"]
        rows = [
            ["Alice", "+919876543210"],
            ["Bob", "+919876543211"],
            ["Alice-again", "+919876543210"],
        ]
        result = CampaignSourceSyncService.validate_source_data(headers, rows)
        assert result.is_valid is True
        assert result.duplicate_count == 1
        assert result.rows == [
            ["Alice", "+919876543210"],
            ["Bob", "+919876543211"],
        ]

    def test_no_duplicates_reports_zero(self):
        headers = ["name", "phone_number"]
        rows = [
            ["Alice", "+919876543210"],
            ["Bob", "+919876543211"],
        ]
        result = CampaignSourceSyncService.validate_source_data(headers, rows)
        assert result.is_valid is True
        assert result.duplicate_count == 0

    def test_invalid_phone_format_still_rejects_before_dedup_runs(self):
        # Missing '+' country code is a separate, still-fatal validation error;
        # dedup should not mask it.
        headers = ["name", "phone_number"]
        rows = [
            ["Alice", "9876543210"],
        ]
        result = CampaignSourceSyncService.validate_source_data(headers, rows)
        assert result.is_valid is False
        assert "country code" in result.error.message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_campaign_source_dedup.py::TestValidateSourceDataDedup -v`
Expected: FAIL — `test_duplicates_are_removed_not_rejected` fails because current code sets `is_valid=False` on duplicates.

- [ ] **Step 3: Replace the reject block with dedup**

In `api/services/campaign/source_sync.py`, replace this block (currently lines ~207-235):

```python
        # Check for duplicate phone numbers
        seen_phones: dict[str, int] = {}  # phone -> first row where it appeared
        duplicate_rows = []
        for row_idx, row in enumerate(rows, start=2):
            if len(row) <= phone_number_idx:
                continue

            phone_number = row[phone_number_idx].strip()
            if not phone_number:
                continue

            if phone_number in seen_phones:
                duplicate_rows.append(row_idx)
            else:
                seen_phones[phone_number] = row_idx

        if duplicate_rows:
            if len(duplicate_rows) > 5:
                rows_str = f"{', '.join(map(str, duplicate_rows[:5]))} and {len(duplicate_rows) - 5} more"
            else:
                rows_str = ", ".join(map(str, duplicate_rows))

            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message=f"Duplicate phone numbers found in rows: {rows_str}. Phone numbers in a campaign must be unique.",
                    invalid_rows=duplicate_rows,
                ),
            )

        return ValidationResult(is_valid=True, headers=normalized_headers, rows=rows)
```

with:

```python
        # Remove duplicate phone numbers (keep first occurrence) instead of
        # rejecting the upload.
        rows, duplicate_count = CampaignSourceSyncService.dedupe_by_phone_number(
            rows, phone_number_idx
        )

        return ValidationResult(
            is_valid=True,
            headers=normalized_headers,
            rows=rows,
            duplicate_count=duplicate_count,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_campaign_source_dedup.py -v`
Expected: PASS (7 tests total)

- [ ] **Step 5: Commit**

```bash
git add api/services/campaign/source_sync.py api/tests/test_campaign_source_dedup.py
git commit -m "feat(campaigns): dedupe duplicate phone numbers instead of rejecting upload"
```

---

### Task 3: `CSVSyncService.sync_source_data` dedupes before creating queued_runs

**Files:**
- Modify: `api/services/campaign/sources/csv.py:82-167` (`sync_source_data`)
- Test: `api/tests/test_campaign_csv_sync_dedup.py` (new file)

**Interfaces:**
- Consumes: `CampaignSourceSyncService.dedupe_by_phone_number(rows, phone_number_idx) -> (deduped_rows, duplicate_count)` from Task 1.
- Produces: `CSVSyncService.sync_source_data(campaign_id)` never creates more than one `queued_runs` row per unique phone number within a single sync pass.

- [ ] **Step 1: Write the failing test**

Create `api/tests/test_campaign_csv_sync_dedup.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_campaign_csv_sync_dedup.py -v`
Expected: FAIL — `test_sync_source_data_dedupes_duplicate_phone_numbers`: `phone_numbers` includes `+919876543210` twice and `total_rows == 3`. `test_sync_source_data_dedupes_after_normalization`: both rows are queued as distinct (dedup doesn't exist yet), `total_rows == 2`.

- [ ] **Step 3: Restructure `sync_source_data` to normalize-then-dedupe before building queued_runs**

`validate_source_data` (Task 2) normalizes phone numbers *before* its dedup step. `sync_source_data` must do the same — dedupe on raw, pre-normalization values would miss numbers that only match after normalization (e.g. a leading zero stripped by `default_country_code` handling), producing a different unique-count than validation saw.

The current code normalizes and pads each row **inside** the `queued_runs`-building loop, one row at a time. To dedupe on normalized values, normalization (and padding) must happen in a pass *before* the loop, so dedup has fully-normalized rows to compare.

In `api/services/campaign/sources/csv.py`, replace this whole block in `sync_source_data`:

```python
        # Get phone number column index so we can normalize it during sync
        phone_number_idx = headers.index("phone_number") if "phone_number" in headers else None

        # Create hash of file_key for consistent source_uuid prefix
        file_hash = hashlib.md5(file_key.encode()).hexdigest()[:8]

        # A re-run of this sync (ARQ retries the job if the worker died or was
        # cancelled mid-insert) must not enqueue the same contacts again —
        # queued_runs has no unique constraint, and duplicates mean every
        # contact gets dialed twice. Skip rows already queued.
        existing_uuids = await db_client.get_existing_source_uuids(campaign_id)

        # Convert to queued_runs
        queued_runs = []
        for idx, row_values in enumerate(rows, 1):
            # Pad row to match headers length
            padded_row = row_values + [""] * (len(headers) - len(row_values))

            # Apply phone normalization to the row if country code is configured
            if phone_number_idx is not None and phone_number_idx < len(padded_row):
                phone_val = padded_row[phone_number_idx]
                padded_row[phone_number_idx] = self.normalize_phone_number(
                    phone_val, default_country_code
                )

            # Create context variables dict
            context_vars = dict(zip(headers, padded_row))

            # Skip if no phone number
            if not context_vars.get("phone_number"):
                logger.debug(f"Skipping row {idx}: no phone_number")
                continue

            # Generate unique source UUID: csv_{file_hash}_row_{idx}
            source_uuid = f"csv_{file_hash}_row_{idx}"

            if source_uuid in existing_uuids:
                continue

            queued_runs.append(
                {
                    "campaign_id": campaign_id,
                    "source_uuid": source_uuid,
                    "context_variables": context_vars,
                    "state": "queued",
                }
            )
```

with:

```python
        # Get phone number column index so we can normalize it during sync
        phone_number_idx = headers.index("phone_number") if "phone_number" in headers else None

        # Pad every row to header length up front (previously done per-row
        # inside the queued_runs loop below — hoisted so normalization and
        # dedup below have fully-shaped rows to work with).
        rows = [
            row_values + [""] * (len(headers) - len(row_values))
            for row_values in rows
        ]

        # Normalize phone numbers, THEN dedupe (keep first occurrence).
        # Normalizing first matches validate_source_data's order, so two rows
        # with the same number in different raw formats (e.g. with/without a
        # leading zero) are recognized as duplicates in both places.
        if phone_number_idx is not None:
            for padded_row in rows:
                if phone_number_idx < len(padded_row):
                    padded_row[phone_number_idx] = self.normalize_phone_number(
                        padded_row[phone_number_idx], default_country_code
                    )
            rows, duplicate_count = self.dedupe_by_phone_number(rows, phone_number_idx)
            if duplicate_count:
                logger.info(
                    f"Removed {duplicate_count} duplicate phone number row(s) "
                    f"for campaign {campaign_id}"
                )

        # Create hash of file_key for consistent source_uuid prefix
        file_hash = hashlib.md5(file_key.encode()).hexdigest()[:8]

        # A re-run of this sync (ARQ retries the job if the worker died or was
        # cancelled mid-insert) must not enqueue the same contacts again —
        # queued_runs has no unique constraint, and duplicates mean every
        # contact gets dialed twice. Skip rows already queued.
        existing_uuids = await db_client.get_existing_source_uuids(campaign_id)

        # Convert to queued_runs
        queued_runs = []
        for idx, padded_row in enumerate(rows, 1):
            # Create context variables dict
            context_vars = dict(zip(headers, padded_row))

            # Skip if no phone number
            if not context_vars.get("phone_number"):
                logger.debug(f"Skipping row {idx}: no phone_number")
                continue

            # Generate unique source UUID: csv_{file_hash}_row_{idx}
            source_uuid = f"csv_{file_hash}_row_{idx}"

            if source_uuid in existing_uuids:
                continue

            queued_runs.append(
                {
                    "campaign_id": campaign_id,
                    "source_uuid": source_uuid,
                    "context_variables": context_vars,
                    "state": "queued",
                }
            )
```

Rows are now padded once up front instead of per-row inside the loop, and the loop variable is renamed from `row_values` to `padded_row` since padding already happened. `idx` (and therefore `source_uuid`) is now based on position in the post-dedup `rows` list rather than the original file — this is safe for the existing re-run idempotency check because dedup is deterministic: re-processing the same unchanged file always drops the same rows and produces the same resulting positions, so `source_uuid` for a given surviving contact stays stable across retries.

- [ ] **Step 4: Run tests to verify they pass**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_campaign_csv_sync_dedup.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add api/services/campaign/sources/csv.py api/tests/test_campaign_csv_sync_dedup.py
git commit -m "feat(campaigns): dedupe duplicate phone numbers when syncing queued_runs"
```

---

### Task 4: Surface `duplicates_removed` on `CampaignResponse`

**Files:**
- Modify: `api/routes/campaign.py` (`CampaignResponse`, `_build_campaign_response`, `create_campaign`)
- Test: `api/tests/test_campaign_response_dedup.py` (new file)

**Interfaces:**
- Consumes: `ValidationResult.duplicate_count` from Task 2.
- Produces: `CampaignResponse.duplicates_removed: Optional[int] = None`; `_build_campaign_response(..., duplicates_removed: Optional[int] = None)`.

- [ ] **Step 1: Write the failing test**

Create `api/tests/test_campaign_response_dedup.py`:

```python
"""_build_campaign_response should pass through a duplicates_removed count
when the caller supplies one, and default to None otherwise.

_build_campaign_response is synchronous (no I/O inside it) — these are
plain, non-async tests."""

from datetime import datetime, timezone
from types import SimpleNamespace

from api.routes.campaign import _build_campaign_response


def _fake_campaign():
    return SimpleNamespace(
        id=1,
        name="Test Campaign",
        workflow_id=1,
        state="draft",
        source_type="csv",
        source_id="contacts.csv",
        total_rows=None,
        processed_rows=0,
        failed_rows=0,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        completed_at=None,
        retry_config=None,
        orchestrator_metadata={},
        telephony_configuration_id=None,
        logs=[],
        organization_id=1,
    )


def test_duplicates_removed_defaults_to_none():
    response = _build_campaign_response(_fake_campaign(), "Test Workflow")
    assert response.duplicates_removed is None


def test_duplicates_removed_is_threaded_through_when_provided():
    response = _build_campaign_response(
        _fake_campaign(), "Test Workflow", duplicates_removed=12
    )
    assert response.duplicates_removed == 12
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_campaign_response_dedup.py -v`
Expected: FAIL with `TypeError: _build_campaign_response() got an unexpected keyword argument 'duplicates_removed'`

- [ ] **Step 3: Add the field and thread it through**

In `api/routes/campaign.py`, add to `CampaignResponse` (right after `source_id: str`):

```python
    source_id: str
    # Count of duplicate-phone-number rows silently removed from the last
    # upload/sync. None when no dedup info is available (e.g. the campaign
    # predates this field, or this response wasn't built from a fresh upload).
    duplicates_removed: Optional[int] = None
```

Add a parameter to `_build_campaign_response`:

```python
def _build_campaign_response(
    campaign,
    workflow_name: str,
    executed_count: int = 0,
    total_queued_count: int = 0,
    telephony_configuration_name: Optional[str] = None,
    total_call_seconds: Optional[int] = None,
    spend_rate_inr_per_minute: float = CAMPAIGN_SPEND_RATE_INR_PER_MINUTE,
    duplicates_removed: Optional[int] = None,
) -> CampaignResponse:
```

and pass it through in the `return CampaignResponse(...)` call (right after `source_id=campaign.source_id,`):

```python
        source_id=campaign.source_id,
        duplicates_removed=duplicates_removed,
```

Finally, in `create_campaign`, pass the count from validation through to the response — modify the existing call:

```python
    return _build_campaign_response(
        campaign,
        workflow_name,
        telephony_configuration_name=cfg_name,
        total_call_seconds=0,  # fresh campaign has no completed runs yet
        spend_rate_inr_per_minute=await _get_org_spend_rate(
            campaign.organization_id
        ),
        duplicates_removed=validation_result.duplicate_count,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/test_campaign_response_dedup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/routes/campaign.py api/tests/test_campaign_response_dedup.py
git commit -m "feat(campaigns): return duplicates_removed count from campaign creation"
```

---

### Task 5: Regenerate client + frontend toast

**Files:**
- Modify: `ui/src/client/types.gen.ts` (auto-generated — via `npm run generate-client`, api server must be running)
- Modify: `ui/src/app/campaigns/new/page.tsx:360`

**Interfaces:**
- Consumes: `CampaignResponse.duplicates_removed: number | null` (regenerated from Task 4's backend change).

- [ ] **Step 1: Regenerate the API client**

With the API server running locally (`uvicorn api.app:app --reload --port 8000`, per `AGENTS.md`), run:

```bash
cd ui && npm run generate-client
```

Expected: `ui/src/client/types.gen.ts`'s `CampaignResponse` type gains a `duplicates_removed?: (number | null)` field. No manual edits needed if the generator ran successfully — verify with:

```bash
grep -A 3 "Duplicates Removed" ui/src/client/types.gen.ts
```

Expected output shows the new field with its JSDoc block.

- [ ] **Step 2: Update the success toast**

In `ui/src/app/campaigns/new/page.tsx`, find the existing success handling (around line 360):

```tsx
                toast.success('Campaign created successfully');
                router.push(`/campaigns/${response.data.id}`);
```

Replace with:

```tsx
                if (response.data.duplicates_removed) {
                    toast.success(
                        `Campaign created — ${response.data.duplicates_removed} duplicate phone number${response.data.duplicates_removed === 1 ? '' : 's'} removed.`
                    );
                } else {
                    toast.success('Campaign created successfully');
                }
                router.push(`/campaigns/${response.data.id}`);
```

- [ ] **Step 3: Type-check the frontend**

Run: `cd ui && npx tsc --noEmit`
Expected: no new type errors.

- [ ] **Step 4: Commit**

```bash
git add ui/src/client/types.gen.ts ui/src/app/campaigns/new/page.tsx
git commit -m "feat(campaigns): show duplicate-removal count in the creation toast"
```

---

## Manual Verification (after all tasks)

1. Start the API and UI locally per `docs/contribution/setup.mdx`.
2. Prepare a CSV with a header row (`name,phone_number`) and at least one duplicate `phone_number` value (both rows fully valid, `+`-prefixed).
3. Upload it in the campaign creation wizard, pick a workflow, submit.
4. Confirm: campaign creation succeeds (no more "Duplicate phone numbers found" error), the toast shows the duplicate count, and on the campaign detail page `total_rows` reflects only unique contacts.
5. Confirm the campaign actually dispatches only one call per unique number (check `queued_runs` count matches unique numbers, e.g. via the campaign's queued-runs list in the UI).
