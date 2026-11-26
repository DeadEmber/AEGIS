"""Append-only double-entry ledger in EUR.

Sign convention: debit = positive amount, credit = negative amount. Every transaction sums to exactly
zero, so money cannot be created or destroyed by bookkeeping. Cash accounts may never go negative
(no leverage, no overdraft). Corrections are made with new reversing transactions, never UPDATE/DELETE.
"""

from __future__ import annotations

import datetime as dt
import enum
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, insert, select
from sqlalchemy.engine import Connection

from aegis.core.clock import ensure_utc
from aegis.core.money import ZERO, is_eur_quantized
from aegis.storage import schema as s


class Account(enum.StrEnum):
    CASH_INVESTMENT = "cash:investment"  # compartment 1: external contributions
    CASH_RECYCLED = "cash:recycled"  # compartment 2: recovered capital available for reinvestment
    CASH_RESERVE_PROFIT = "cash:reserve:profit"  # compartment 3a: profit reserve (policy)
    CASH_RESERVE_TAX = "cash:reserve:tax"  # compartment 3b: estimated tax provision (never reinvested)
    POSITIONS = "asset:positions"  # open lots at cost (acquisition costs capitalised)
    DEPOSITS = "asset:deposits"  # recoverable rent deposits of token accounts
    EQUITY_CONTRIBUTIONS = "equity:contributions"
    EQUITY_WITHDRAWALS = "equity:withdrawals"
    PNL_REALIZED = "pnl:realized"  # credit = gain, debit = loss
    EXPENSE_NETWORK = "expense:network"  # fees not capitalised (e.g. closing accounts)
    EXPENSE_TECHNOLOGY = "expense:technology"
    EXPENSE_TAX_PAID = "expense:tax_paid"


CASH_ACCOUNTS = (
    Account.CASH_INVESTMENT,
    Account.CASH_RECYCLED,
    Account.CASH_RESERVE_PROFIT,
    Account.CASH_RESERVE_TAX,
)
# Accounts from which purchases may be funded. Reserves are deliberately excluded.
SPENDABLE_ACCOUNTS = (Account.CASH_INVESTMENT, Account.CASH_RECYCLED)


class LedgerError(Exception):
    pass


class UnbalancedTransaction(LedgerError):
    pass


class InsufficientFunds(LedgerError):
    pass


@dataclass(frozen=True)
class Posting:
    account: Account
    amount: Decimal
    memo: str | None = None


@dataclass(frozen=True)
class PostedTransaction:
    id: int
    created: bool  # False when the idempotency key already existed


class Ledger:
    def post(
        self,
        conn: Connection,
        *,
        occurred_at: dt.datetime,
        recorded_at: dt.datetime,
        kind: str,
        description: str,
        postings: Sequence[Posting],
        idempotency_key: str,
        ref_type: str | None = None,
        ref_id: int | None = None,
    ) -> PostedTransaction:
        existing = conn.execute(
            select(s.ledger_transactions.c.id).where(s.ledger_transactions.c.idempotency_key == idempotency_key)
        ).first()
        if existing is not None:
            return PostedTransaction(int(existing.id), created=False)
        clean = [p for p in postings if p.amount != ZERO]
        if len(clean) < 2:
            raise UnbalancedTransaction(f"{kind}: needs at least two non-zero postings")
        for p in clean:
            if not isinstance(p.amount, Decimal):
                raise LedgerError("posting amounts must be Decimal")
            if not is_eur_quantized(p.amount):
                raise LedgerError(f"posting amount {p.amount} not quantized to the ledger scale (1e-12 EUR)")
            Account(p.account)  # validates
        total = sum((p.amount for p in clean), ZERO)
        if total != ZERO:
            raise UnbalancedTransaction(f"{kind}: postings sum to {total}, expected 0")
        # No negative cash: check every cash account touched by this transaction.
        deltas: dict[Account, Decimal] = {}
        for p in clean:
            deltas[p.account] = deltas.get(p.account, ZERO) + p.amount
        for acct, delta in deltas.items():
            if acct in CASH_ACCOUNTS and delta < 0:
                bal = self.balance(conn, acct)
                if bal + delta < ZERO:
                    raise InsufficientFunds(f"{acct}: balance {bal} insufficient for {-delta}")
        tx_id = conn.execute(
            insert(s.ledger_transactions).values(
                occurred_at=ensure_utc(occurred_at),
                recorded_at=ensure_utc(recorded_at),
                kind=kind,
                description=description,
                ref_type=ref_type,
                ref_id=ref_id,
                idempotency_key=idempotency_key,
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(s.ledger_postings),
            [
                {"transaction_id": tx_id, "account": str(p.account), "amount_eur": p.amount, "memo": p.memo}
                for p in clean
            ],
        )
        return PostedTransaction(int(tx_id), created=True)

    def balance(self, conn: Connection, account: Account, as_of: dt.datetime | None = None) -> Decimal:
        # Decimal SUM in SQL is unsafe on SQLite (stored as TEXT) -> sum in Python, exactly.
        q = select(s.ledger_postings.c.amount_eur).where(s.ledger_postings.c.account == str(account))
        if as_of is not None:
            q = q.join(s.ledger_transactions).where(s.ledger_transactions.c.occurred_at <= ensure_utc(as_of))
        return sum((r.amount_eur for r in conn.execute(q)), ZERO)

    def balances(self, conn: Connection) -> dict[Account, Decimal]:
        out = {a: ZERO for a in Account}
        for r in conn.execute(select(s.ledger_postings.c.account, s.ledger_postings.c.amount_eur)):
            out[Account(r.account)] += r.amount_eur
        return out

    def trial_balance_ok(self, conn: Connection) -> bool:
        return sum(self.balances(conn).values(), ZERO) == ZERO

    def transaction_count(self, conn: Connection) -> int:
        return int(conn.execute(select(func.count()).select_from(s.ledger_transactions)).scalar_one())

    @staticmethod
    def transfer(src: Account, dst: Account, amount: Decimal, memo: str | None = None) -> list[Posting]:
        return [Posting(dst, amount, memo), Posting(src, -amount, memo)]


def split_funding(
    available: dict[Account, Decimal], amount: Decimal, order: Iterable[Account]
) -> list[tuple[Account, Decimal]]:
    """Split `amount` across spendable accounts in the given priority order."""
    remaining = amount
    out: list[tuple[Account, Decimal]] = []
    for acct in order:
        if acct not in SPENDABLE_ACCOUNTS:
            raise LedgerError(f"{acct} is not spendable")
        take = min(available.get(acct, ZERO), remaining)
        if take > ZERO:
            out.append((acct, take))
            remaining -= take
        if remaining == ZERO:
            return out
    raise InsufficientFunds(f"cannot fund {amount}: short by {remaining}")
