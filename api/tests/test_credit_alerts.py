"""Threshold, runway and send-once logic for low-credit alerts.

The riskiest behaviours here are the ones that are silent when wrong: alerting
an unmetered org (who has unlimited calling), spamming the same org daily, or
failing to re-arm after a top-up. Those get explicit tests.
"""

import pytest

from api.services.credit_alerts import (
    build_alert_email,
    credit_alert_already_sent,
    estimate_runway_days,
    format_minutes,
    format_runway,
    select_alert_stage,
)


class TestSelectAlertStage:
    def test_unmetered_org_is_never_alerted(self):
        # NULL balance == unlimited. Alerting these clients would be a lie.
        assert select_alert_stage(None) is None

    def test_healthy_balance_does_not_alert(self):
        assert select_alert_stage(60 * 60) is None  # 1 hour left

    def test_just_above_first_floor_does_not_alert(self):
        assert select_alert_stage(30 * 60 + 1) is None

    def test_at_thirty_minute_floor(self):
        assert select_alert_stage(30 * 60) == "30min"

    def test_between_floors_reports_the_higher_floor(self):
        assert select_alert_stage(20 * 60) == "30min"

    def test_at_ten_minute_floor(self):
        assert select_alert_stage(10 * 60) == "10min"

    def test_most_severe_floor_wins(self):
        # 0 crosses all three floors; it must report 'empty', not '30min'.
        assert select_alert_stage(0) == "empty"

    def test_negative_balance_is_empty_not_healthy(self):
        # Overdraft from a settled call that overran its reservation.
        assert select_alert_stage(-120) == "empty"


class TestEstimateRunwayDays:
    def test_unmetered_has_no_runway(self):
        assert estimate_runway_days(None, 3600) is None

    def test_empty_balance_has_no_runway(self):
        assert estimate_runway_days(0, 3600) is None

    def test_zero_burn_returns_none_rather_than_infinity(self):
        # An org that hasn't called in the window would otherwise divide by zero.
        assert estimate_runway_days(1800, 0) is None

    def test_typical_burn(self):
        # 7000s spent over 7 days = 1000s/day; 3000s left ≈ 3 days.
        assert estimate_runway_days(3000, 7000) == pytest.approx(3.0)


class TestFormatting:
    def test_minutes(self):
        assert format_minutes(600) == "10 minutes"
        assert format_minutes(60) == "1 minute"
        assert format_minutes(0) == "0 minutes"

    def test_hours(self):
        assert format_minutes(3600) == "about 1 hour"
        assert format_minutes(7200) == "about 2 hours"

    def test_runway_phrasing(self):
        assert format_runway(None) is None
        assert format_runway(0.5) == "less than a day at your current usage"
        assert format_runway(1.4) == "about a day at your current usage"
        assert format_runway(3.0) == "about 3 days at your current usage"


class TestBuildAlertEmail:
    def test_empty_stage_mentions_campaigns_are_paused_not_failed(self):
        subject, body = build_alert_email("empty", 0, None, "https://x/credits")
        assert "run out" in subject
        assert "paused" in body
        assert "https://x/credits" in body

    def test_warning_includes_balance_and_runway(self):
        subject, body = build_alert_email(
            "10min", 600, "about 2 days at your current usage", "https://x/credits"
        )
        assert "10 minutes" in subject
        assert "about 2 days at your current usage" in body

    def test_warning_omits_runway_cleanly_when_unknown(self):
        subject, body = build_alert_email("30min", 1800, None, "https://x/credits")
        # No dangling separator when there's no runway to report.
        assert "—" not in body
        assert "30 minutes" in subject


class TestSendOnceBookkeeping:
    def test_first_send_is_not_suppressed(self):
        assert credit_alert_already_sent({}, "30min", cycle=1800) is False

    def test_same_stage_same_cycle_is_suppressed(self):
        profile = {"credit_alerts_sent": {"cycle": 1800, "stages": ["30min"]}}
        assert credit_alert_already_sent(profile, "30min", 1800) is True

    def test_a_more_severe_stage_still_fires_in_the_same_cycle(self):
        # Having warned at 30 min must not silence the 'empty' alert.
        profile = {"credit_alerts_sent": {"cycle": 1800, "stages": ["30min"]}}
        assert credit_alert_already_sent(profile, "empty", 1800) is False

    def test_topup_rearms_a_previously_sent_stage(self):
        # Cycle key = lifetime credited seconds, so a top-up changes it.
        profile = {"credit_alerts_sent": {"cycle": 1800, "stages": ["30min", "empty"]}}
        assert credit_alert_already_sent(profile, "30min", 5400) is False
