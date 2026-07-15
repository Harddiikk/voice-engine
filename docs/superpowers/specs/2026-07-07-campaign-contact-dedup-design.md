# Campaign contact dedup by phone number

## Problem

When a user uploads contacts (CSV/Excel) for a campaign, `validate_source_data`
(`api/services/campaign/source_sync.py`) currently **rejects the entire upload**
if any phone number appears more than once, forcing the user to manually clean
the file and re-upload before they can create the campaign.

Separately, `sync_source_data` (`api/services/campaign/sources/csv.py`) — the
method that actually creates the `queued_runs` rows that get dialed — re-parses
the file independently and has **no duplicate check at all**. So even if
validation were relaxed, duplicate numbers would still be queued and dialed
more than once.

## Goal

Uploading a contact list with duplicate phone numbers should not block campaign
creation. Duplicates are silently deduplicated (keeping the first occurrence of
each phone number) so only unique contacts are queued, and the user sees a
one-time count of how many were removed.

## Design

### 1. Shared dedup helper

Add a static method to `CampaignSourceSyncService` (`api/services/campaign/source_sync.py`):

```python
@staticmethod
def dedupe_by_phone_number(
    rows: List[List[str]], phone_number_idx: int
) -> tuple[List[List[str]], int]:
    """Keep the first row for each phone number; drop later duplicates.
    Rows with no value at phone_number_idx pass through unchanged (existing
    empty-phone handling elsewhere is unaffected). Returns (deduped_rows, duplicate_count)."""
```

Keeps the first occurrence (per the confirmed decision — matches the existing
`seen_phones` tracking already in `validate_source_data`).

### 2. `validate_source_data` — dedupe instead of reject

Replace the current "Duplicate phone numbers found in rows: ... reject" block
(`source_sync.py:207-235`) with a call to `dedupe_by_phone_number`. The
validation no longer fails on duplicates; it returns `is_valid=True` with the
deduped `rows` and a new field on `ValidationResult`:

```python
duplicate_count: Optional[int] = None
```

### 3. `sync_source_data` — dedupe before creating queued_runs

In `csv.py`'s `sync_source_data`, call the same `dedupe_by_phone_number` on the
parsed `rows` (using the already-computed `phone_number_idx`) before the
per-row loop that builds `queued_runs`. This guarantees the actual dispatch
path never double-dials a number, independent of whatever validation saw
earlier (the file could theoretically change between preview/validate and the
background sync task running).

### 4. Surfacing the count

- `CampaignResponse` (`api/routes/campaign.py`) gets a new optional field
  `duplicates_removed: Optional[int] = None`.
- `create_campaign` reads `validation_result.duplicate_count` and threads it
  into the response.
- Frontend (`ui/src/app/campaigns/new/page.tsx:360`) checks
  `response.data.duplicates_removed`: if truthy, shows
  `toast.success(\`Campaign created — ${n} duplicate phone number(s) removed.\`)`
  instead of the current generic `'Campaign created successfully'`.
- Regenerate the API client (`npm run generate-client`) after the backend
  schema change.

## Out of scope

- Cross-campaign dedup (this only dedupes within a single upload).
- Non-CSV sources — `CSVSyncService` is currently the only implementation of
  `CampaignSourceSyncService`, so no other source type needs changes.
- Changing `normalize_phone_number` behavior — dedup keys on the
  already-normalized phone value (post country-code prefixing), so e.g.
  `9876543210` and `+919876543210` are only recognized as the same number if
  normalization already made them identical strings before dedup runs (true
  today, since normalization happens earlier in both call sites).

## Testing

- Unit test `dedupe_by_phone_number` directly: first-occurrence-wins, empty
  phone values pass through, no duplicates is a no-op.
- Unit test `validate_source_data` with a duplicate-containing fixture: expect
  `is_valid=True`, `duplicate_count` matching, deduped `rows`.
- Unit test `CSVSyncService.sync_source_data` with a duplicate-containing CSV:
  expect `queued_runs` created only for unique numbers.
