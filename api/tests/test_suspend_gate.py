"""Suspend gate: no-op when the org isn't suspended, 403 ``account_suspended``
when it is. ``is_org_suspended`` is mocked so the gate is tested in isolation
(no DB / admin-profile dependency)."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.services.admin import suspend_gate
from api.services.admin.suspend_gate import (
    SUSPENDED_CODE,
    SUSPENDED_MESSAGE,
    assert_org_not_suspended,
)


async def test_not_suspended_is_noop():
    with patch.object(
        suspend_gate, "is_org_suspended", new=AsyncMock(return_value=False)
    ):
        await assert_org_not_suspended(4)  # must not raise


async def test_suspended_raises_403_with_code_and_message():
    with patch.object(
        suspend_gate, "is_org_suspended", new=AsyncMock(return_value=True)
    ):
        with pytest.raises(HTTPException) as exc:
            await assert_org_not_suspended(4)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == SUSPENDED_CODE == "account_suspended"
    assert exc.value.detail["message"] == SUSPENDED_MESSAGE


async def test_none_org_is_passed_through():
    """A missing org id resolves to 'not suspended' (no profile) — the gate must
    delegate that decision to is_org_suspended rather than guessing."""
    m = AsyncMock(return_value=False)
    with patch.object(suspend_gate, "is_org_suspended", new=m):
        await assert_org_not_suspended(None)
    m.assert_awaited_once_with(None)
