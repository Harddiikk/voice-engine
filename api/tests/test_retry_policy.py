"""Phase 4a: escalating retry delays."""

from api.services.campaign.retry_policy import retry_delay_for_attempt


def test_fixed_delay_when_no_list():
    cfg = {"retry_delay_seconds": 120}
    assert retry_delay_for_attempt(cfg, 0) == 120
    assert retry_delay_for_attempt(cfg, 5) == 120


def test_escalating_list_by_attempt():
    cfg = {"retry_delay_seconds": 120, "retry_delays_seconds": [120, 900, 3600]}
    assert retry_delay_for_attempt(cfg, 0) == 120   # 1st retry -> 2m
    assert retry_delay_for_attempt(cfg, 1) == 900   # 2nd retry -> 15m
    assert retry_delay_for_attempt(cfg, 2) == 3600  # 3rd retry -> 1h


def test_last_entry_repeats_beyond_list():
    cfg = {"retry_delays_seconds": [120, 900, 3600]}
    assert retry_delay_for_attempt(cfg, 3) == 3600
    assert retry_delay_for_attempt(cfg, 9) == 3600


def test_empty_list_falls_back_to_fixed():
    assert retry_delay_for_attempt({"retry_delay_seconds": 200, "retry_delays_seconds": []}, 0) == 200


def test_invalid_entry_falls_back_to_fixed():
    cfg = {"retry_delay_seconds": 150, "retry_delays_seconds": [0]}
    assert retry_delay_for_attempt(cfg, 0) == 150


def test_default_when_nothing_set():
    assert retry_delay_for_attempt({}, 0) == 120


def test_followup_context_line_per_reason():
    from api.services.campaign.retry_policy import followup_context_line

    assert "voicemail" in followup_context_line("voicemail").lower()
    assert "busy" in followup_context_line("busy").lower()
    assert "answer" in followup_context_line("no_answer").lower()
    assert "connect" in followup_context_line("failed").lower()
    # Unknown reason -> a generic follow-up line (never empty/crash).
    assert followup_context_line("whatever").strip() != ""
