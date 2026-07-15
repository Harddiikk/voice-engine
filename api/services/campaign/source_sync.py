import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

# GPC is an India-only deployment; numbers without an explicit country code
# are normalized to +91.
DEFAULT_COUNTRY_CODE = "+91"


@dataclass
class ValidationError:
    """Represents a validation error with details."""

    message: str
    invalid_rows: Optional[List[int]] = None


@dataclass
class ValidationResult:
    """Result of source validation."""

    is_valid: bool
    error: Optional[ValidationError] = None
    headers: Optional[List[str]] = field(default=None, repr=False)
    rows: Optional[List[List[str]]] = field(default=None, repr=False)
    # Duplicate-phone rows silently dropped during validation (kept rows win).
    duplicates_removed: int = 0


class CampaignSourceSyncService(ABC):
    """Base class for campaign data source synchronization"""

    @staticmethod
    def normalize_headers(headers: List[str]) -> List[str]:
        """Normalize headers by stripping whitespace and lowercasing."""
        return [h.strip().lower() for h in headers]

    @staticmethod
    def normalize_phone_number(
        phone: str, default_country_code: Optional[str] = DEFAULT_COUNTRY_CODE
    ) -> str:
        """Strip spaces/dashes/parens; auto-prefix the country code.

        Handles the formats Indian sheets actually contain: ``98765 43210``,
        ``09876543210`` (trunk zero), ``919876543210`` (cc without '+'),
        ``00919876543210`` (international prefix) — all collapse to
        ``+919876543210``. Numbers already starting with '+' are untouched.
        """
        val = (phone or "").strip()
        if not val:
            return val
        normalized = re.sub(r"[\s\-()]", "", val)
        if normalized.startswith("+"):
            return normalized

        if default_country_code:
            cc = default_country_code.strip()
            if not cc.startswith("+"):
                cc = "+" + cc
            cc_digits = cc[1:]

            if normalized.startswith("00"):
                normalized = normalized[2:]
            elif normalized.startswith("0"):
                normalized = normalized[1:]

            # cc already present without '+' (cc + 10-digit national number)
            if normalized.startswith(cc_digits) and len(normalized) == len(
                cc_digits
            ) + 10:
                return f"+{normalized}"

            return f"{cc}{normalized}"

        return normalized

    @staticmethod
    def normalize_and_dedupe_rows(
        rows: List[List[str]],
        phone_number_idx: int,
        default_country_code: Optional[str] = DEFAULT_COUNTRY_CODE,
    ) -> Tuple[List[List[str]], int]:
        """Normalize every phone in place, then drop later duplicate-phone rows.

        Returns (kept_rows, duplicates_removed). Rows with no/short phone pass
        through untouched and are never deduped against each other. Kept rows
        preserve their original order (and thus original row positions).
        """
        seen_phones: set = set()
        kept: List[List[str]] = []
        duplicates_removed = 0
        for row in rows:
            if len(row) <= phone_number_idx or not row[phone_number_idx].strip():
                kept.append(row)
                continue
            normalized = CampaignSourceSyncService.normalize_phone_number(
                row[phone_number_idx], default_country_code
            )
            if normalized in seen_phones:
                duplicates_removed += 1
                continue
            seen_phones.add(normalized)
            row = list(row)
            row[phone_number_idx] = normalized
            kept.append(row)
        return kept, duplicates_removed

    @staticmethod
    def validate_source_data(
        headers: List[str],
        rows: List[List[str]],
        default_country_code: Optional[str] = DEFAULT_COUNTRY_CODE,
    ) -> ValidationResult:
        """
        Validate source data for campaign creation.

        Phone numbers are auto-normalized (default +91) and duplicate-phone
        rows are silently dropped (first occurrence wins) rather than rejected.

        Args:
            headers: List of column headers
            rows: List of data rows (excluding header)
            default_country_code: prefix for numbers without '+'

        Returns:
            ValidationResult with is_valid=True if valid, or error details if invalid
        """
        normalized_headers = CampaignSourceSyncService.normalize_headers(headers)

        # Check for phone_number column
        if "phone_number" not in normalized_headers:
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message="Source must contain a 'phone_number' column"
                ),
            )

        phone_number_idx = normalized_headers.index("phone_number")

        # Normalize + dedupe first, then validate what's left.
        rows, duplicates_removed = (
            CampaignSourceSyncService.normalize_and_dedupe_rows(
                rows, phone_number_idx, default_country_code
            )
        )

        # Validate phone numbers in all data rows (post-normalization)
        invalid_rows = []
        for row_idx, row in enumerate(
            rows, start=2
        ):  # Start at 2 (1-indexed, skip header)
            if len(row) <= phone_number_idx:
                continue  # Skip rows that don't have enough columns

            phone_number = row[phone_number_idx].strip()
            if phone_number and not phone_number.startswith("+"):
                invalid_rows.append(row_idx)

        if invalid_rows:
            # Limit the number of rows shown in error message
            if len(invalid_rows) > 5:
                rows_str = f"{', '.join(map(str, invalid_rows[:5]))} and {len(invalid_rows) - 5} more"
            else:
                rows_str = ", ".join(map(str, invalid_rows))

            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message=f"Invalid phone numbers in rows: {rows_str}. All phone numbers must include country code (start with '+')",
                    invalid_rows=invalid_rows,
                ),
            )

        return ValidationResult(
            is_valid=True,
            headers=normalized_headers,
            rows=rows,
            duplicates_removed=duplicates_removed,
        )

    @staticmethod
    def validate_template_columns(
        headers: List[str],
        rows: List[List[str]],
        required_columns: Set[str],
    ) -> ValidationResult:
        """Validate that template variable columns exist and are non-empty in all rows."""
        normalized_headers = CampaignSourceSyncService.normalize_headers(headers)

        # Check for missing columns
        missing = required_columns - set(normalized_headers)
        if missing:
            missing_str = ", ".join(f"'{c}'" for c in sorted(missing))
            return ValidationResult(
                is_valid=False,
                error=ValidationError(
                    message=f"Workflow uses template variables that are missing from the source data: {missing_str}. "
                    "Add the missing columns or remove them from the workflow."
                ),
            )

        # Check for empty values in required columns
        col_indices = {col: normalized_headers.index(col) for col in required_columns}

        for col, idx in col_indices.items():
            empty_rows = []
            for row_idx, row in enumerate(rows, start=2):
                if len(row) <= idx or not row[idx].strip():
                    empty_rows.append(row_idx)

            if empty_rows:
                if len(empty_rows) > 5:
                    rows_str = f"{', '.join(map(str, empty_rows[:5]))} and {len(empty_rows) - 5} more"
                else:
                    rows_str = ", ".join(map(str, empty_rows))

                return ValidationResult(
                    is_valid=False,
                    error=ValidationError(
                        message=f"Template variable '{col}' is empty in rows: {rows_str}. "
                        "All template variables used in the workflow must have values in every row.",
                        invalid_rows=empty_rows,
                    ),
                )

        return ValidationResult(is_valid=True)

    @abstractmethod
    async def validate_source(
        self, source_id: str, organization_id: Optional[int] = None
    ) -> ValidationResult:
        """Validate source data before campaign creation."""
        pass

    @abstractmethod
    async def sync_source_data(self, campaign_id: int) -> int:
        """
        Fetches data from source and creates queued_runs
        Each record gets a unique source_uuid based on source type
        Returns: number of records synced
        """
        pass

    async def get_source_credentials(
        self, organization_id: int, source_type: str
    ) -> Dict[str, Any]:
        """Gets source credentials when a sync service requires them."""
        logger.info(
            f"Getting credentials for org {organization_id}, source {source_type}"
        )
        return {}
