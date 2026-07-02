"""Tests for campaign calling-window schedule helpers.

Covers the pure helpers in ``api.services.campaign.schedule``:

- ``slot_matches`` — per-slot matching incl. overnight (start > end) windows
- ``is_within_schedule`` — the full check the orchestrator's
  ``_is_within_schedule`` delegates to (mixed slot days, fail-open behavior)
- ``default_schedule_config`` — parsing of DEFAULT_CAMPAIGN_CALLING_WINDOW

The orchestrator method itself is a thin delegate to ``is_within_schedule``
(importing the orchestrator module pulls in the full pipecat service factory,
which isn't importable in the test venv), so the pure helper is tested
directly here. No DB access — everything is pure/injected-clock.
"""

from datetime import UTC, datetime

from api import constants
from api.services.campaign.schedule import (
    default_schedule_config,
    is_within_schedule,
    slot_matches,
)


def _slot(day: int, start: str, end: str) -> dict:
    return {"day_of_week": day, "start_time": start, "end_time": end}


class TestSlotMatchesSameDay:
    def test_matches_inside_window(self):
        assert slot_matches(_slot(2, "09:00", "17:00"), 2, "10:00") is True

    def test_no_match_before_window(self):
        assert slot_matches(_slot(2, "09:00", "17:00"), 2, "08:59") is False

    def test_no_match_other_day(self):
        assert slot_matches(_slot(2, "09:00", "17:00"), 3, "10:00") is False

    def test_start_boundary_matches(self):
        assert slot_matches(_slot(2, "09:00", "17:00"), 2, "09:00") is True

    def test_end_boundary_does_not_match(self):
        # Half-open interval: t == end is outside the window.
        assert slot_matches(_slot(2, "09:00", "17:00"), 2, "17:00") is False


class TestSlotMatchesOvernight:
    """start > end wraps past midnight: [start, 24:00) on the slot's day,
    [00:00, end) on the next day."""

    OVERNIGHT = _slot(4, "22:00", "02:00")  # Friday 22:00 -> Saturday 02:00

    def test_matches_late_evening_on_slot_day(self):
        assert slot_matches(self.OVERNIGHT, 4, "23:00") is True

    def test_matches_early_morning_next_day(self):
        assert slot_matches(self.OVERNIGHT, 5, "01:30") is True

    def test_no_match_next_day_after_end(self):
        assert slot_matches(self.OVERNIGHT, 5, "03:00") is False

    def test_no_match_on_slot_day_before_start(self):
        assert slot_matches(self.OVERNIGHT, 4, "21:59") is False

    def test_start_boundary_matches(self):
        assert slot_matches(self.OVERNIGHT, 4, "22:00") is True

    def test_end_boundary_next_day_does_not_match(self):
        assert slot_matches(self.OVERNIGHT, 5, "02:00") is False

    def test_wraps_from_sunday_to_monday(self):
        # day_of_week 6 (Sunday) overnight slot matches Monday (0) morning.
        assert slot_matches(_slot(6, "22:00", "02:00"), 0, "01:00") is True

    def test_no_match_two_days_later(self):
        assert slot_matches(self.OVERNIGHT, 6, "01:00") is False


class TestSlotMatchesDegenerate:
    def test_start_equals_end_never_matches(self):
        # Documented: a zero-length window never matches, even exactly at t.
        assert slot_matches(_slot(1, "09:00", "09:00"), 1, "09:00") is False

    def test_missing_day_never_matches(self):
        assert (
            slot_matches({"start_time": "09:00", "end_time": "17:00"}, 0, "10:00")
            is False
        )

    def test_missing_times_never_match(self):
        assert slot_matches({"day_of_week": 0}, 0, "10:00") is False


class TestIsWithinSchedule:
    """Full schedule check: must consider ALL slots, since yesterday's
    overnight slot can still cover this morning."""

    # 2026-07-01 is a Wednesday (weekday 2).
    WEDNESDAY_0130_UTC = datetime(2026, 7, 1, 1, 30, tzinfo=UTC)
    WEDNESDAY_0300_UTC = datetime(2026, 7, 1, 3, 0, tzinfo=UTC)
    WEDNESDAY_1000_UTC = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    MIXED_SLOTS_CONFIG = {
        "enabled": True,
        "timezone": "UTC",
        "slots": [
            _slot(1, "22:00", "02:00"),  # Tuesday overnight into Wednesday
            _slot(2, "09:00", "17:00"),  # Wednesday daytime
        ],
    }

    def test_yesterdays_overnight_slot_matches_this_morning(self):
        assert self.WEDNESDAY_0130_UTC.weekday() == 2  # sanity: Wednesday
        assert (
            is_within_schedule(self.MIXED_SLOTS_CONFIG, now=self.WEDNESDAY_0130_UTC)
            is True
        )

    def test_gap_between_overnight_end_and_daytime_start(self):
        assert (
            is_within_schedule(self.MIXED_SLOTS_CONFIG, now=self.WEDNESDAY_0300_UTC)
            is False
        )

    def test_daytime_slot_matches(self):
        assert (
            is_within_schedule(self.MIXED_SLOTS_CONFIG, now=self.WEDNESDAY_1000_UTC)
            is True
        )

    def test_timezone_conversion_applies(self):
        # 01:30 UTC on Wednesday is 07:00 IST — outside a Tue-overnight
        # 22:00-02:00 IST window, inside a Wed 06:30-08:00 IST window.
        config = {
            "enabled": True,
            "timezone": "Asia/Kolkata",
            "slots": [_slot(1, "22:00", "02:00")],
        }
        assert is_within_schedule(config, now=self.WEDNESDAY_0130_UTC) is False
        config["slots"] = [_slot(2, "06:30", "08:00")]
        assert is_within_schedule(config, now=self.WEDNESDAY_0130_UTC) is True

    # --- fail-open behavior must be preserved ---

    def test_missing_schedule_config_allows(self):
        assert is_within_schedule(None) is True
        assert is_within_schedule({}) is True

    def test_disabled_schedule_allows(self):
        config = {**self.MIXED_SLOTS_CONFIG, "enabled": False}
        assert is_within_schedule(config, now=self.WEDNESDAY_0300_UTC) is True

    def test_no_slots_allows(self):
        config = {"enabled": True, "timezone": "UTC", "slots": []}
        assert is_within_schedule(config, now=self.WEDNESDAY_0300_UTC) is True

    def test_invalid_timezone_fails_open(self):
        config = {**self.MIXED_SLOTS_CONFIG, "timezone": "Not/AZone"}
        assert is_within_schedule(config, now=self.WEDNESDAY_0300_UTC) is True


class TestDefaultScheduleConfig:
    def test_shipped_default_window(self, monkeypatch):
        monkeypatch.setattr(constants, "DEFAULT_CAMPAIGN_CALLING_WINDOW", "09:00-21:00")
        monkeypatch.setattr(
            constants, "DEFAULT_CAMPAIGN_CALLING_TIMEZONE", "Asia/Kolkata"
        )
        config = default_schedule_config()
        assert config is not None
        assert config["enabled"] is True
        assert config["timezone"] == "Asia/Kolkata"
        assert len(config["slots"]) == 7
        assert [slot["day_of_week"] for slot in config["slots"]] == list(range(7))
        assert all(slot["start_time"] == "09:00" for slot in config["slots"])
        assert all(slot["end_time"] == "21:00" for slot in config["slots"])

    def test_custom_overnight_window_and_timezone(self, monkeypatch):
        monkeypatch.setattr(constants, "DEFAULT_CAMPAIGN_CALLING_WINDOW", "21:30-06:00")
        monkeypatch.setattr(constants, "DEFAULT_CAMPAIGN_CALLING_TIMEZONE", "UTC")
        config = default_schedule_config()
        assert config is not None
        assert config["timezone"] == "UTC"
        assert all(slot["start_time"] == "21:30" for slot in config["slots"])
        assert all(slot["end_time"] == "06:00" for slot in config["slots"])

    def test_empty_window_disables_default(self, monkeypatch):
        monkeypatch.setattr(constants, "DEFAULT_CAMPAIGN_CALLING_WINDOW", "")
        assert default_schedule_config() is None

    def test_unparsable_window_disables_default(self, monkeypatch):
        for bad in ("9am-9pm", "09:00", "09:00-17:00-21:00", "9:00-17:00"):
            monkeypatch.setattr(constants, "DEFAULT_CAMPAIGN_CALLING_WINDOW", bad)
            assert default_schedule_config() is None, bad

    def test_zero_length_window_disables_default(self, monkeypatch):
        monkeypatch.setattr(constants, "DEFAULT_CAMPAIGN_CALLING_WINDOW", "09:00-09:00")
        assert default_schedule_config() is None

    def test_default_config_matches_route_schema_shape(self):
        # The default must satisfy the API's ScheduleConfigRequest rules:
        # zero-padded HH:MM times, day_of_week 0-6, start != end (overnight
        # start > end is allowed). Asserted structurally — importing
        # api.routes.campaign pulls the pipecat service factory, which isn't
        # importable in the test venv.
        import re

        config = default_schedule_config()
        assert config is not None
        time_re = re.compile(r"^\d{2}:\d{2}$")
        for slot in config["slots"]:
            assert 0 <= slot["day_of_week"] <= 6
            assert time_re.match(slot["start_time"])
            assert time_re.match(slot["end_time"])
            assert slot["start_time"] != slot["end_time"]
