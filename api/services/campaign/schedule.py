"""Pure helpers for campaign calling-window schedules.

A campaign's ``schedule_config`` (stored in ``orchestrator_metadata``) looks
like::

    {
        "enabled": True,
        "timezone": "Asia/Kolkata",
        "slots": [
            {"day_of_week": 0, "start_time": "09:00", "end_time": "21:00"},
            ...
        ],
    }

Times are zero-padded ``"HH:MM"`` strings (comparable lexicographically) and
``day_of_week`` follows Python's ``datetime.weekday()`` convention
(0=Monday .. 6=Sunday).

These helpers are intentionally pure (no DB, no clock) so both the
orchestrator and tests can use them directly.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from api import constants

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def slot_matches(slot: dict, current_day: int, current_time: str) -> bool:
    """Return True when (current_day, current_time) falls inside ``slot``.

    Semantics:

    - ``start < end`` — a normal same-day window: matches when today is the
      slot's day and ``start <= t < end``.
    - ``start > end`` — an *overnight* window that wraps past midnight (e.g.
      ``22:00-02:00``): matches on the slot's day from ``start`` onwards, OR
      on the *next* day before ``end``.
    - ``start == end`` — a zero-length window: never matches. An "all day"
      window should be expressed as ``00:00-23:59`` instead.

    Boundary behavior mirrors the historical check: ``t == start`` matches,
    ``t == end`` does not (half-open interval).
    """
    slot_day = slot.get("day_of_week")
    if not isinstance(slot_day, int):
        return False

    start = slot.get("start_time", "")
    end = slot.get("end_time", "")
    if not start or not end:
        return False

    if start < end:
        return current_day == slot_day and start <= current_time < end
    if start > end:
        # Overnight wrap: [start, midnight) on the slot's day, then
        # [midnight, end) on the following day.
        return (current_day == slot_day and current_time >= start) or (
            current_day == (slot_day + 1) % 7 and current_time < end
        )
    # start == end: zero-length window, documented as never matching.
    return False


def is_within_schedule(
    schedule_config: dict | None,
    now: datetime | None = None,
    campaign_id: int | None = None,
) -> bool:
    """Return True when calls are currently allowed under ``schedule_config``.

    Fail-open semantics (returns True) when:
    - ``schedule_config`` is missing/empty
    - the schedule is disabled
    - no slots are configured
    - the timezone is invalid (logged as a warning)

    Otherwise the current wall-clock time in the schedule's timezone is
    matched against EVERY slot (not just slots whose ``day_of_week`` is
    today): an overnight slot from *yesterday* (start > end) can still cover
    this morning.

    ``now`` (an aware datetime) can be injected for tests; defaults to the
    real clock. ``campaign_id`` is only used for log context.
    """
    if not schedule_config:
        return True

    if not schedule_config.get("enabled", False):
        return True

    slots = schedule_config.get("slots")
    if not slots:
        return True

    timezone_str = schedule_config.get("timezone", "UTC")
    try:
        tz = ZoneInfo(timezone_str)
    except (KeyError, Exception):
        logger.warning(
            f"campaign_id: {campaign_id} - Invalid timezone '{timezone_str}' in schedule_config, "
            f"failing open (allowing scheduling)"
        )
        return True

    local_now = now.astimezone(tz) if now is not None else datetime.now(tz)
    current_day = local_now.weekday()  # 0=Monday through 6=Sunday
    current_time = local_now.strftime("%H:%M")

    return any(slot_matches(slot, current_day, current_time) for slot in slots)


def default_schedule_config() -> dict | None:
    """Build the default ``schedule_config`` for campaigns created without one.

    Parses ``constants.DEFAULT_CAMPAIGN_CALLING_WINDOW`` (``"HH:MM-HH:MM"``,
    overnight ``start > end`` allowed) into a 7-day schedule in
    ``constants.DEFAULT_CAMPAIGN_CALLING_TIMEZONE``.

    Returns None (no default schedule — calls allowed at any hour) when the
    window is empty, unparsable, or zero-length (``start == end``).
    """
    window = (constants.DEFAULT_CAMPAIGN_CALLING_WINDOW or "").strip()
    if not window:
        return None

    parts = window.split("-")
    if len(parts) != 2:
        return None

    start, end = (part.strip() for part in parts)
    if not _TIME_RE.match(start) or not _TIME_RE.match(end):
        return None
    if start == end:
        # Zero-length window never matches any time; treat as "no default"
        # rather than silently creating a campaign that can never dial.
        return None

    return {
        "enabled": True,
        "timezone": constants.DEFAULT_CAMPAIGN_CALLING_TIMEZONE,
        "slots": [
            {"day_of_week": day, "start_time": start, "end_time": end}
            for day in range(7)
        ],
    }
