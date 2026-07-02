"""One-off repair for the credit money-core rollout. Idempotent.

Does two things:

(a) Seeds an opening ``adjustment`` ledger row (idempotency key
    ``opening:{org_id}``) for every metered org that doesn't have one, so the
    invariant SUM(delta_seconds) == balance holds from day one.
(b) Settles JSON-era leaked holds: completed runs whose reservation lives in
    ``initial_context.reserved_credit_seconds`` but that were never settled
    (no JSON ``credits_settled`` flag, no ``credits_settled_at``). Settling
    releases the stranded hold and charges the run's actual duration, via the
    same exactly-once service the live system uses (origin='repair').

Finally prints per-org before/after balances and the SUM(delta)==balance
invariant check.

Usage (against the env's DB — source api/.env first):

    source venv/bin/activate && set -a && source api/.env && set +a && \
        python -m api.scripts.repair_leaked_credit_holds --dry-run
    ... --apply   # actually write
"""

import argparse
import asyncio
import sys

from api.db import db_client
from api.services.credits.reservation import (
    RESERVED_CREDIT_SECONDS_KEY,
    settle_workflow_run_credits,
)


async def _metered_org_balances() -> dict[int, int]:
    rows = await db_client.execute_raw_query(
        """
        SELECT id, free_call_seconds_remaining AS balance
        FROM organizations
        WHERE free_call_seconds_remaining IS NOT NULL
        ORDER BY id
        """
    )
    return {int(r["id"]): int(r["balance"]) for r in rows}


async def _orgs_missing_opening_row() -> list[dict]:
    return await db_client.execute_raw_query(
        """
        SELECT o.id, o.free_call_seconds_remaining AS balance
        FROM organizations o
        WHERE o.free_call_seconds_remaining IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM credit_ledger l
              WHERE l.idempotency_key = 'opening:' || o.id::text
          )
        ORDER BY o.id
        """
    )


async def _invariant_report() -> list[dict]:
    return await db_client.execute_raw_query(
        """
        SELECT o.id,
               o.free_call_seconds_remaining AS balance,
               COALESCE(SUM(l.delta_seconds), 0) AS ledger_sum
        FROM organizations o
        LEFT JOIN credit_ledger l ON l.organization_id = o.id
        WHERE o.free_call_seconds_remaining IS NOT NULL
        GROUP BY o.id, o.free_call_seconds_remaining
        ORDER BY o.id
        """
    )


async def run(apply: bool) -> int:
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"== repair_leaked_credit_holds [{mode}] ==\n")

    balances_before = await _metered_org_balances()

    # ---- (a) opening ledger rows ----
    missing = await _orgs_missing_opening_row()
    print(f"(a) Metered orgs missing an opening ledger row: {len(missing)}")
    for row in missing:
        org_id, balance = int(row["id"]), int(row["balance"])
        if apply:
            delta = await db_client.record_opening_balance_tx(org_id)
            print(
                f"    org {org_id}: opening adjustment inserted "
                f"(delta={delta}, balance={balance})"
            )
        else:
            print(f"    org {org_id}: would insert opening delta={balance}")

    # ---- (b) JSON-era leaked holds ----
    leaked = await db_client.list_json_era_leaked_runs()
    print(f"\n(b) JSON-era leaked holds (completed, never settled): {len(leaked)}")
    settled = 0
    for workflow_run, organization_id in leaked:
        ctx = getattr(workflow_run, "initial_context", None) or {}
        reserved = ctx.get(RESERVED_CREDIT_SECONDS_KEY) or 0
        usage = getattr(workflow_run, "usage_info", None) or {}
        cost = getattr(workflow_run, "cost_info", None) or {}
        duration = (
            usage.get("call_duration_seconds")
            or cost.get("call_duration_seconds")
            or 0
        )
        if apply:
            try:
                outcome = await settle_workflow_run_credits(
                    organization_id, workflow_run, origin="repair"
                )
                if outcome == "settled":
                    settled += 1
                print(
                    f"    run {workflow_run.id} (org {organization_id}): "
                    f"reserved={reserved}s actual={duration}s -> {outcome}"
                )
            except Exception as exc:
                print(
                    f"    run {workflow_run.id} (org {organization_id}): "
                    f"FAILED to settle: {exc}"
                )
        else:
            print(
                f"    run {workflow_run.id} (org {organization_id}): would settle "
                f"reserved={reserved}s actual={duration}s "
                f"(net refund {max(0, int(reserved) - int(duration or 0))}s)"
            )
    if apply:
        print(f"    settled {settled}/{len(leaked)}")

    # ---- per-org before/after ----
    balances_after = await _metered_org_balances()
    changed = {
        org_id: (balances_before.get(org_id), balances_after.get(org_id))
        for org_id in sorted(set(balances_before) | set(balances_after))
        if balances_before.get(org_id) != balances_after.get(org_id)
    }
    print(f"\nPer-org balance changes: {len(changed)}")
    for org_id, (before, after) in changed.items():
        print(f"    org {org_id}: {before} -> {after}")

    # ---- invariant: SUM(delta_seconds) == balance per metered org ----
    print("\nInvariant check (SUM(credit_ledger.delta_seconds) == balance):")
    report = await _invariant_report()
    mismatches = [
        r for r in report if int(r["ledger_sum"] or 0) != int(r["balance"] or 0)
    ]
    for r in report:
        marker = "OK " if r not in mismatches else "MISMATCH"
        print(
            f"    org {r['id']}: balance={r['balance']} "
            f"ledger_sum={r['ledger_sum']} [{marker}]"
        )
    if mismatches:
        if apply:
            print(f"\nWARNING: {len(mismatches)} org(s) violate the invariant.")
            return 1
        print(
            f"\nNOTE: {len(mismatches)} org(s) currently off (expected before "
            f"--apply seeds opening rows / settles leaks)."
        )
    else:
        print("\nAll metered orgs satisfy the invariant.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed opening credit-ledger rows and settle JSON-era leaked holds."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run", action="store_true", help="report only, write nothing"
    )
    group.add_argument("--apply", action="store_true", help="write the repairs")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(apply=args.apply)))


if __name__ == "__main__":
    main()
