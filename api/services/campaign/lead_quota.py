"""Pure helpers for the per-run lead quota ("call the next N leads").

Mirrors budget.py: the quota lives in campaign ``orchestrator_metadata`` as
``lead_quota`` (the N chosen at start/resume) vs ``lead_quota_used`` (running
counter of first-attempt dispatches in the current window). Scheduled retries
never consume quota — they re-dial leads that were already counted.
"""


def lead_quota_remaining(orchestrator_metadata: dict | None) -> int | None:
    """Leads still allowed in the current run window; None = no quota set."""
    meta = orchestrator_metadata or {}
    quota = meta.get("lead_quota")
    if not quota:
        return None
    used = int(meta.get("lead_quota_used", 0) or 0)
    return max(0, int(quota) - used)


def lead_quota_exhausted(orchestrator_metadata: dict | None) -> bool:
    """True when the current window's quota is used up (auto-pause signal)."""
    remaining = lead_quota_remaining(orchestrator_metadata)
    return remaining is not None and remaining <= 0


def open_quota_window(
    orchestrator_metadata: dict | None, call_limit: int | None
) -> tuple[dict, bool]:
    """New metadata for a fresh quota window at start/resume.

    ``call_limit=N`` sets the quota; ``None`` clears it (unlimited). The
    used-counter resets either way. Returns ``(new_meta, changed)`` —
    ``changed`` is False when there was no quota before and none is being
    set, so callers can skip the DB write.
    """
    meta = dict(orchestrator_metadata or {})
    had_quota_keys = "lead_quota" in meta or "lead_quota_used" in meta
    if call_limit:
        meta["lead_quota"] = int(call_limit)
    else:
        meta.pop("lead_quota", None)
    meta.pop("lead_quota_used", None)
    return meta, bool(call_limit) or had_quota_keys
