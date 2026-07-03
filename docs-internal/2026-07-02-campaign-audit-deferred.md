# Campaign audit 2026-07-02 — deferred findings

Full audit ran pre-launch (4 critical / 6 high / 8 medium). Seven fixes shipped in
`aacac0d` (resume-from-failed, idempotent CSV sync, breaker storm-default 0.9/20,
arq job_timeout 900s, spend first-terminal-only bump, unreleased-definition guard,
concurrency int-cast). The rest is deliberately deferred:

## High-value next (post-campaign)

- **Leaked credit holds (C2, metered orgs only).** Three paths reserve 600s and
  never settle: dial-initiation failure (`campaign_call_dispatcher.py` dispatch
  failure path marks run completed, no settle), terminal `completed` webhook for a
  run whose pipeline never ran (`status_processor.py` COMPLETED branch never
  enqueues integrations), api restart mid-call. Fix: enqueue the settle task in
  both paths + a periodic sweep for completed runs with `reserved_credit_seconds`
  set and `credits_settled` unset. Unmetered (NULL) orgs are immune.
- **In-run retries never fire on VoiceLink (H5).** Retries publish only for
  BUSY/NO_ANSWER, which VoiceLink never emits (only initiated/ringing/answered/
  completed/ended/failed). Map Q.850 `hangupCause` 17→busy, 18/19→no-answer in
  `parse_status_callback`. Until then `retry_config` is inert; use post-campaign
  Redial.
- **Reservation-failure contact burn (C1, metered).** Reservation failure inside a
  batch marks the contact permanently failed and the trial gate pauses at 0
  balance. Keep balance ≥ 600×(concurrency+1) or run unmetered. Proper fix:
  return claim to queued + pause on `insufficient_credits`.

## Also open

- SIGKILL mid-batch strands `processing` claims forever (H3) — needs startup/cron
  sweep resetting stale processing rows without workflow runs.
- Unsigned VoiceLink webhooks, guessable integer run ids (M6).
- Double-settle race on concurrent duplicate webhooks (M3) — read-then-write
  `credits_settled` flag; org gains a hold. Needs compare-and-set.
- `processed_rows` undercounts (M1, stale per-batch object) — progress % wrong in UI.
- Pause during `syncing` gets overridden to running when sync completes (M2).
- `queued_runs` lacks a unique constraint on (campaign_id, source_uuid) — code-level
  guard shipped; add the index + ON CONFLICT when a migration window is safe.
- No dedicated tests yet for resume-from-failed and CSV-sync dedupe (shipped
  under deadline; covered by A/B against pre-existing suite).

## Open question (answer via smoke test)

Does VoiceLink report an unanswered call as `call.failed` (settles fine, counts
as breaker failure) or `call.completed` with duration 0 (leaks the hold on
metered orgs, counts as success)? Let one smoke-test call ring out and check the
run's terminal state.
