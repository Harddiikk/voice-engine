"""Pure helpers for campaign retry timing (no DB, no clock, no heavy imports)."""

# Human-readable opener context per retry reason, injected as the
# ``{{followup_context}}`` template variable so an agent's greeting/prompt can
# open differently on a follow-up call WITHOUT conditional templating (the
# renderer is substitution-only). Empty on first attempts (the var is absent).
_FOLLOWUP_LINES = {
    "voicemail": "This is a follow-up call — the previous attempt reached their voicemail.",
    "no_answer": "This is a follow-up call — they did not answer the previous attempt.",
    "busy": "This is a follow-up call — the line was busy on the previous attempt.",
    "failed": "This is a follow-up call — the previous attempt could not connect.",
}


def followup_context_line(reason: str) -> str:
    """A short natural-language opener for a retry call (empty for unknown reason)."""
    return _FOLLOWUP_LINES.get(reason, "This is a follow-up call.")


def retry_delay_for_attempt(retry_config: dict, current_retry_count: int) -> int:
    """Seconds to wait before the next retry attempt.

    ``retry_delays_seconds`` (a list, e.g. [120, 900, 3600]) gives escalating
    delays: index ``current_retry_count`` selects the wait before the next
    attempt, and the last entry repeats for any further attempts. Falls back to
    the fixed ``retry_delay_seconds`` (default 120) when the list is absent or
    empty; an invalid/non-positive entry also falls back to the fixed value.
    """
    delays = retry_config.get("retry_delays_seconds")
    if isinstance(delays, list) and delays:
        idx = min(max(0, current_retry_count), len(delays) - 1)
        try:
            value = int(delays[idx])
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return int(retry_config.get("retry_delay_seconds", 120))
