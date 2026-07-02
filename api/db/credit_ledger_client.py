"""Transactional money primitives for the org call-seconds credit system.

Every public ``*_tx`` method here is ONE database transaction: the balance
mutation (``UPDATE organizations ... RETURNING free_call_seconds_remaining``),
its append-only ``credit_ledger`` row, and any state CAS (run settled marker /
payment paid marker) commit or roll back together — no more crash windows
between "money moved" and "we recorded that money moved".

Conventions:

- NULL balance = unmetered/unlimited org. Balance mutations are always guarded
  with ``free_call_seconds_remaining IS NOT NULL`` so an unmetered org is never
  converted to metered; such no-ops produce NO ledger rows.
- ``idempotency_key`` is UNIQUE on ``credit_ledger``. A retried mutation that
  would double-apply instead raises IntegrityError → the whole transaction is
  rolled back and the ``ALREADY_APPLIED`` sentinel is returned.
- ``delta_seconds`` records the APPLIED delta (e.g. a floored charge on a low
  balance records only what was actually deducted), so SUM(delta_seconds) per
  org tracks the balance exactly.
"""

from datetime import UTC, datetime
from typing import List, Optional, Union

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from api.db.base_client import BaseDBClient
from api.db.models import (
    CreditLedgerModel,
    OrganizationModel,
    PaymentTransactionModel,
    WorkflowModel,
    WorkflowRunModel,
)

# Returned by *_tx methods when the mutation was already applied (idempotency
# key hit / CAS lost) — callers treat it as success-without-side-effects.
ALREADY_APPLIED = "already"
# Returned when the org is unmetered (NULL balance): nothing was charged and
# the caller should proceed as "allowed, free".
UNMETERED = "unmetered"

_BALANCE = OrganizationModel.free_call_seconds_remaining


class CreditLedgerClient(BaseDBClient):
    # ---- internal helpers (call within an open session/transaction) ----

    def _ledger_row(
        self,
        session,
        *,
        organization_id: int,
        kind: str,
        delta_seconds: int,
        balance_after: Optional[int],
        workflow_run_id: Optional[int] = None,
        campaign_id: Optional[int] = None,
        payment_transaction_id: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        description: Optional[str] = None,
        created_by: Optional[int] = None,
    ) -> None:
        session.add(
            CreditLedgerModel(
                organization_id=organization_id,
                kind=kind,
                delta_seconds=delta_seconds,
                balance_after=balance_after,
                workflow_run_id=workflow_run_id,
                campaign_id=campaign_id,
                payment_transaction_id=payment_transaction_id,
                idempotency_key=idempotency_key,
                description=description,
                created_by=created_by,
            )
        )

    async def _locked_balance(self, session, organization_id: int) -> Optional[int]:
        """Read the org balance under a row lock (orders concurrent mutations)."""
        result = await session.execute(
            select(_BALANCE)
            .where(OrganizationModel.id == organization_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    # ---- money mutations (one transaction each) ----

    async def reserve_run_credits_tx(
        self, organization_id: int, workflow_run_id: int, seconds: int
    ) -> Union[int, str, None]:
        """Atomically hold ``seconds`` for an in-flight run.

        Conditional debit (only when the metered balance covers it) + ledger
        row + ``workflow_runs.reserved_credit_seconds`` in one transaction.

        Returns the reserved seconds on success, ``ALREADY_APPLIED`` when this
        run already holds a reservation (retried authorization), or None when
        the balance cannot cover the hold (also for unmetered orgs — callers
        check for NULL balance first and never reserve for them).
        """
        if seconds <= 0:
            return None
        idempotency_key = f"run:{workflow_run_id}:reserve"
        async with self.async_session() as session:
            try:
                # A retried authorization must see its existing hold BEFORE the
                # conditional debit — after the first reserve the balance may
                # no longer cover a second debit, which would misreport an
                # already-held run as "insufficient". A concurrent duplicate
                # that slips past this check still dies on the UNIQUE key.
                existing = await session.execute(
                    select(CreditLedgerModel.id).where(
                        CreditLedgerModel.idempotency_key == idempotency_key
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    await session.rollback()
                    return ALREADY_APPLIED

                result = await session.execute(
                    update(OrganizationModel)
                    .where(
                        OrganizationModel.id == organization_id,
                        _BALANCE.isnot(None),
                        _BALANCE >= seconds,
                    )
                    .values(free_call_seconds_remaining=_BALANCE - seconds)
                    .returning(_BALANCE)
                )
                new_balance = result.scalar_one_or_none()
                if new_balance is None:
                    await session.rollback()
                    return None
                self._ledger_row(
                    session,
                    organization_id=organization_id,
                    kind="reserve",
                    delta_seconds=-seconds,
                    balance_after=new_balance,
                    workflow_run_id=workflow_run_id,
                    idempotency_key=idempotency_key,
                )
                await session.execute(
                    update(WorkflowRunModel)
                    .where(WorkflowRunModel.id == workflow_run_id)
                    .values(reserved_credit_seconds=seconds)
                )
                await session.commit()
                return seconds
            except IntegrityError:
                await session.rollback()
                return ALREADY_APPLIED

    async def settle_run_credits_tx(
        self,
        organization_id: int,
        workflow_run_id: int,
        reserved_seconds: int,
        actual_seconds: Union[int, float, None],
        origin: str = "settle",
        description: Optional[str] = None,
    ) -> str:
        """Settle a run exactly once: release its hold, charge actual usage.

        The FIRST statement is a CAS on ``workflow_runs.credits_settled_at``
        (set now() only where still NULL) — a retried/concurrent settle loses
        the CAS and returns ``ALREADY_APPLIED`` without touching money. Then,
        for metered orgs only: release ``reserved_seconds`` back, charge the
        actual duration floored at zero, writing one ledger row per applied
        non-zero delta. Unmetered orgs get no balance changes and no ledger
        rows but are still marked settled.

        ``origin`` tags who settled: 'settle' (post-call task / dial-failure
        inline), 'sweeper' (leak cron) or 'repair' (backfill script); sweeper/
        repair releases are recorded as kind='leak_sweep' for auditability.
        """
        release_kind = (
            "leak_sweep" if origin in ("sweeper", "repair") else "settle_release"
        )
        try:
            actual = max(0, int(round(float(actual_seconds or 0))))
        except (TypeError, ValueError):
            actual = 0
        reserved = max(0, int(reserved_seconds or 0))

        async with self.async_session() as session:
            try:
                cas = await session.execute(
                    update(WorkflowRunModel)
                    .where(
                        WorkflowRunModel.id == workflow_run_id,
                        WorkflowRunModel.credits_settled_at.is_(None),
                    )
                    .values(credits_settled_at=datetime.now(UTC))
                )
                if cas.rowcount == 0:
                    await session.rollback()
                    return ALREADY_APPLIED

                # Lock the org row so release + charge see a consistent balance.
                balance = await self._locked_balance(session, organization_id)

                if balance is not None and reserved > 0:
                    result = await session.execute(
                        update(OrganizationModel)
                        .where(
                            OrganizationModel.id == organization_id,
                            _BALANCE.isnot(None),
                        )
                        .values(free_call_seconds_remaining=_BALANCE + reserved)
                        .returning(_BALANCE)
                    )
                    released_balance = result.scalar_one_or_none()
                    if released_balance is not None:
                        self._ledger_row(
                            session,
                            organization_id=organization_id,
                            kind=release_kind,
                            delta_seconds=reserved,
                            balance_after=released_balance,
                            workflow_run_id=workflow_run_id,
                            idempotency_key=f"run:{workflow_run_id}:release",
                            description=description,
                        )
                        balance = released_balance

                if balance is not None and actual > 0:
                    result = await session.execute(
                        update(OrganizationModel)
                        .where(
                            OrganizationModel.id == organization_id,
                            _BALANCE.isnot(None),
                        )
                        .values(
                            free_call_seconds_remaining=func.greatest(
                                _BALANCE - actual, 0
                            )
                        )
                        .returning(_BALANCE)
                    )
                    charged_balance = result.scalar_one_or_none()
                    if charged_balance is not None:
                        applied = charged_balance - balance  # <= 0; floored at 0
                        if applied != 0:
                            charge_description = (
                                f"{description} (requested {actual}s)"
                                if description
                                else f"requested {actual}s"
                            )
                            self._ledger_row(
                                session,
                                organization_id=organization_id,
                                kind="settle_charge",
                                delta_seconds=applied,
                                balance_after=charged_balance,
                                workflow_run_id=workflow_run_id,
                                idempotency_key=f"run:{workflow_run_id}:settle",
                                description=charge_description,
                            )

                await session.commit()
                return "settled"
            except IntegrityError:
                await session.rollback()
                return ALREADY_APPLIED

    async def topup_paid_tx(
        self, gateway_order_id: str, gateway_payment_id: str
    ) -> str:
        """Mark a payment txn paid and credit its seconds, atomically + once.

        CAS on ``payment_transactions.status`` (→ 'paid' only where not already
        'paid') guards double-crediting across concurrent verify/callback/
        webhook deliveries. Unmetered orgs are marked paid but NOT credited
        (never convert unlimited to metered) → ``UNMETERED``.
        """
        async with self.async_session() as session:
            try:
                cas = await session.execute(
                    update(PaymentTransactionModel)
                    .where(
                        PaymentTransactionModel.razorpay_order_id == gateway_order_id,
                        PaymentTransactionModel.status != "paid",
                    )
                    .values(
                        status="paid",
                        razorpay_payment_id=gateway_payment_id,
                        updated_at=datetime.now(UTC),
                    )
                    .returning(
                        PaymentTransactionModel.id,
                        PaymentTransactionModel.organization_id,
                        PaymentTransactionModel.seconds,
                        PaymentTransactionModel.pack_id,
                    )
                )
                row = cas.first()
                if row is None:
                    await session.rollback()
                    return ALREADY_APPLIED
                txn_id, organization_id, seconds, pack_id = row

                result = await session.execute(
                    update(OrganizationModel)
                    .where(
                        OrganizationModel.id == organization_id,
                        _BALANCE.isnot(None),
                    )
                    .values(
                        free_call_seconds_remaining=_BALANCE + max(0, int(seconds))
                    )
                    .returning(_BALANCE)
                )
                new_balance = result.scalar_one_or_none()
                if new_balance is None:
                    # Unmetered org: record the payment, never meter the org.
                    await session.commit()
                    return UNMETERED

                self._ledger_row(
                    session,
                    organization_id=organization_id,
                    kind="topup",
                    delta_seconds=max(0, int(seconds)),
                    balance_after=new_balance,
                    payment_transaction_id=txn_id,
                    idempotency_key=f"order:{gateway_order_id}",
                    description=(
                        f"Top-up {pack_id or 'pack'} (+{max(0, int(seconds))}s)"
                    ),
                )
                await session.commit()
                return "credited"
            except IntegrityError:
                await session.rollback()
                return ALREADY_APPLIED

    async def grant_credits_tx(
        self,
        organization_id: int,
        seconds: int,
        created_by: Optional[int],
        description: Optional[str] = None,
    ) -> Optional[int]:
        """Credit a metered org (admin grant) + ledger row; returns new balance.

        None for unmetered orgs (no-op — never convert unlimited to metered)
        or a non-positive amount.
        """
        if seconds <= 0:
            return None
        async with self.async_session() as session:
            try:
                result = await session.execute(
                    update(OrganizationModel)
                    .where(
                        OrganizationModel.id == organization_id,
                        _BALANCE.isnot(None),
                    )
                    .values(free_call_seconds_remaining=_BALANCE + seconds)
                    .returning(_BALANCE)
                )
                new_balance = result.scalar_one_or_none()
                if new_balance is None:
                    await session.rollback()
                    return None
                self._ledger_row(
                    session,
                    organization_id=organization_id,
                    kind="grant",
                    delta_seconds=seconds,
                    balance_after=new_balance,
                    created_by=created_by,
                    description=description,
                )
                await session.commit()
                return new_balance
            except IntegrityError:
                await session.rollback()
                return None

    async def charge_purchase_tx(
        self,
        organization_id: int,
        seconds: int,
        kind: str,
        description: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Union[int, str, None]:
        """Charge a one-off purchase (e.g. a phone number) against the balance.

        Returns the new balance (int) when charged, ``UNMETERED`` when the org
        is unmetered or there is nothing to charge (callers proceed for free),
        ``ALREADY_APPLIED`` when the idempotency key was already used, or None
        when the metered balance cannot cover the charge.
        """
        if seconds <= 0:
            return UNMETERED
        async with self.async_session() as session:
            try:
                balance = await self._locked_balance(session, organization_id)
                if balance is None:
                    await session.rollback()
                    return UNMETERED
                result = await session.execute(
                    update(OrganizationModel)
                    .where(
                        OrganizationModel.id == organization_id,
                        _BALANCE.isnot(None),
                        _BALANCE >= seconds,
                    )
                    .values(free_call_seconds_remaining=_BALANCE - seconds)
                    .returning(_BALANCE)
                )
                new_balance = result.scalar_one_or_none()
                if new_balance is None:
                    await session.rollback()
                    return None
                self._ledger_row(
                    session,
                    organization_id=organization_id,
                    kind=kind,
                    delta_seconds=-seconds,
                    balance_after=new_balance,
                    idempotency_key=idempotency_key,
                    description=description,
                )
                await session.commit()
                return new_balance
            except IntegrityError:
                await session.rollback()
                return ALREADY_APPLIED

    async def refund_tx(
        self,
        organization_id: int,
        seconds: int,
        description: Optional[str] = None,
    ) -> Optional[int]:
        """Credit back a failed purchase; returns the new balance.

        None for unmetered orgs (nothing was charged) or non-positive amounts.
        """
        if seconds <= 0:
            return None
        async with self.async_session() as session:
            try:
                result = await session.execute(
                    update(OrganizationModel)
                    .where(
                        OrganizationModel.id == organization_id,
                        _BALANCE.isnot(None),
                    )
                    .values(free_call_seconds_remaining=_BALANCE + seconds)
                    .returning(_BALANCE)
                )
                new_balance = result.scalar_one_or_none()
                if new_balance is None:
                    await session.rollback()
                    return None
                self._ledger_row(
                    session,
                    organization_id=organization_id,
                    kind="refund",
                    delta_seconds=seconds,
                    balance_after=new_balance,
                    description=description,
                )
                await session.commit()
                return new_balance
            except IntegrityError:
                await session.rollback()
                return None

    # ---- reads ----

    async def list_ledger_entries(
        self,
        organization_id: int,
        limit: int = 50,
        offset: int = 0,
        kind: Optional[str] = None,
    ) -> List[CreditLedgerModel]:
        """Org-scoped ledger page, newest first."""
        async with self.async_session() as session:
            query = (
                select(CreditLedgerModel)
                .where(CreditLedgerModel.organization_id == organization_id)
                .order_by(
                    CreditLedgerModel.created_at.desc(), CreditLedgerModel.id.desc()
                )
                .limit(limit)
                .offset(offset)
            )
            if kind:
                query = query.where(CreditLedgerModel.kind == kind)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def sum_on_hold_seconds(self, organization_id: int) -> int:
        """Total seconds currently reserved by the org's unsettled in-flight runs."""
        async with self.async_session() as session:
            result = await session.execute(
                select(func.coalesce(func.sum(WorkflowRunModel.reserved_credit_seconds), 0))
                .select_from(WorkflowRunModel)
                .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
                .where(
                    WorkflowModel.organization_id == organization_id,
                    WorkflowRunModel.reserved_credit_seconds.isnot(None),
                    WorkflowRunModel.credits_settled_at.is_(None),
                    WorkflowRunModel.is_completed.isnot(True),
                )
            )
            return int(result.scalar_one() or 0)

    # ---- repair / backfill helpers (used by scripts) ----

    async def record_opening_balance_tx(self, organization_id: int) -> Optional[int]:
        """Seed the org's ledger with an opening 'adjustment' row.

        The opening delta is ``balance - SUM(existing deltas)`` (== balance
        when the ledger is empty) so that afterwards SUM(delta_seconds) ==
        balance for the org. Idempotent via ``opening:{org_id}``; returns the
        inserted delta, or None when the org is unmetered or already seeded.
        """
        async with self.async_session() as session:
            try:
                balance = await self._locked_balance(session, organization_id)
                if balance is None:
                    await session.rollback()
                    return None
                existing = (
                    await session.execute(
                        select(
                            func.coalesce(func.sum(CreditLedgerModel.delta_seconds), 0)
                        ).where(CreditLedgerModel.organization_id == organization_id)
                    )
                ).scalar_one()
                delta = int(balance) - int(existing or 0)
                self._ledger_row(
                    session,
                    organization_id=organization_id,
                    kind="adjustment",
                    delta_seconds=delta,
                    balance_after=balance,
                    idempotency_key=f"opening:{organization_id}",
                    description="Opening balance (pre-ledger history)",
                )
                await session.commit()
                return delta
            except IntegrityError:
                await session.rollback()
                return None

    async def list_json_era_leaked_runs(self, limit: int = 5000) -> List[tuple]:
        """(workflow_run, organization_id) for pre-migration leaked holds.

        JSON-era runs stored the hold in ``initial_context.reserved_credit_
        seconds`` (column is NULL) and never got the JSON ``credits_settled``
        flag — their reservation was debited but never released/charged.
        """
        ic = WorkflowRunModel.initial_context
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowRunModel, WorkflowModel.organization_id)
                .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
                .where(
                    ic.op("->>")("reserved_credit_seconds").isnot(None),
                    ic.op("->>")("credits_settled").is_distinct_from("true"),
                    WorkflowRunModel.reserved_credit_seconds.is_(None),
                    WorkflowRunModel.credits_settled_at.is_(None),
                    WorkflowRunModel.is_completed.is_(True),
                )
                .order_by(WorkflowRunModel.created_at)
                .limit(limit)
            )
            return [(run, org_id) for run, org_id in result.all()]

    async def list_unsettled_credit_holds(
        self,
        *,
        completed_cutoff: datetime,
        stale_cutoff: datetime,
        limit: int = 500,
    ) -> List[tuple]:
        """(workflow_run, organization_id) pairs whose holds leaked.

        A hold has leaked when it is unsettled AND either its run finished
        (is_completed) more than the grace window ago, or the run is simply
        older than the stale cutoff (stuck INITIALIZED/RUNNING forever).
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowRunModel, WorkflowModel.organization_id)
                .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
                .where(
                    WorkflowRunModel.reserved_credit_seconds.isnot(None),
                    WorkflowRunModel.credits_settled_at.is_(None),
                    or_(
                        and_(
                            WorkflowRunModel.is_completed.is_(True),
                            WorkflowRunModel.created_at < completed_cutoff,
                        ),
                        WorkflowRunModel.created_at < stale_cutoff,
                    ),
                )
                .order_by(WorkflowRunModel.created_at)
                .limit(limit)
            )
            return [(run, org_id) for run, org_id in result.all()]
