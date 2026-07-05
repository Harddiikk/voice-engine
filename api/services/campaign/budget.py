"""Pure helper for the per-campaign spend cap (no DB, no imports)."""


def campaign_budget_exhausted(orchestrator_metadata: dict | None) -> bool:
    """True when the campaign has consumed its budget (auto-pause signal).

    Reads ``budget_seconds`` (the cap) vs ``consumed_seconds`` (the running
    counter) from the campaign's ``orchestrator_metadata``. No cap set → never
    exhausted.
    """
    meta = orchestrator_metadata or {}
    budget_seconds = meta.get("budget_seconds")
    if not budget_seconds:
        return False
    consumed = int(meta.get("consumed_seconds", 0) or 0)
    return consumed >= int(budget_seconds)
