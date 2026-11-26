"""Portfolio Accounting Engine.

Owns positions, FIFO lots, disposals, contributions, reserves, valuation snapshots and
reconciliation. Every money movement goes through `Ledger.post` (double entry, idempotent).
All methods take an open SQLAlchemy Connection and run inside the caller's transaction, so a
worker cycle is atomic: either all its bookings persist or none do.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Connection

from aegis.accounting.ledger import (
    CASH_ACCOUNTS,
    SPENDABLE_ACCOUNTS,
    Account,
    InsufficientFunds,
    Ledger,
    Posting,
    split_funding,
)
from aegis.accounting.tax_es import estimate_by_year
from aegis.config import StrategyConfig
from aegis.core.clock import ensure_utc, month_key
from aegis.core.money import ONE, ZERO, q_eur
from aegis.execution.types import BuyFill, SellFill
from aegis.storage import schema as s

OPERATIONAL = "operational"


class PositionStatus:
    OPEN = "OPEN"
    CAPITAL_RECOVERED = "CAPITAL_RECOVERED"
    RESIDUAL = "RESIDUAL"
    DUST = "DUST"
    CLOSED = "CLOSED"

    ACTIVE = (OPEN, CAPITAL_RECOVERED, RESIDUAL, DUST)


@dataclass(frozen=True)
class SaleResult:
    position_id: int
    qty_sold: Decimal
    cost_basis_sold: Decimal
    net_proceeds: Decimal
    realized_gain: Decimal
    deposit_refund: Decimal
    closed: bool


@dataclass(frozen=True)
class PositionValuation:
    position_id: int
    mark_eur: Decimal | None  # qty * indicative price (NOT liquidable)
    liquidation_eur: Decimal | None  # net EUR receivable if fully sold now (after impact & exit fees)
    method: str  # jupiter_quote | cpmm_estimate | stale_haircut | unvalued
    observed_at: dt.datetime | None
    stale: bool = False


@dataclass
class ReconciliationReport:
    ok: bool
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, name: str, ok: bool, **info: Any) -> None:
        self.checks[name] = {"ok": ok, **{k: str(v) for k, v in info.items()}}
        if not ok:
            self.ok = False


@dataclass(frozen=True)
class PortfolioState:
    as_of: dt.datetime
    cash: dict[str, Decimal]
    deposits: Decimal
    positions_cost: Decimal
    positions_mark: Decimal
    positions_liquidation: Decimal
    open_positions: int
    stale_positions: int
    unvalued_positions: int
    contributions_total: Decimal
    withdrawals_total: Decimal
    realized_pnl_total: Decimal
    costs_total: Decimal
    est_tax_realized_ytd: Decimal
    net_worth_liquidable: Decimal
    objective: Decimal
    reconciliation: ReconciliationReport

    @property
    def cash_total(self) -> Decimal:
        return sum(self.cash.values(), ZERO)


class PortfolioService:
    def __init__(self, strategy: StrategyConfig, ledger: Ledger | None = None):
        self.cfg = strategy
        self.ledger = ledger or Ledger()

    # ----------------------------------------------------------------- external flows
    def contribute_month(self, conn: Connection, t: dt.datetime, amount: Decimal | None = None) -> bool:
        """Book the monthly external contribution once per calendar month (UTC). Idempotent."""
        period = month_key(t)
        if conn.execute(select(s.contributions.c.id).where(s.contributions.c.period == period)).first():
            return False
        amount = q_eur(self.cfg.capital.monthly_contribution_eur if amount is None else amount)
        if amount <= ZERO:
            return False
        tx = self.ledger.post(
            conn,
            occurred_at=t,
            recorded_at=t,
            kind="contribution",
            description=f"Monthly virtual contribution {period}",
            postings=[Posting(Account.CASH_INVESTMENT, amount), Posting(Account.EQUITY_CONTRIBUTIONS, -amount)],
            idempotency_key=f"contribution:{period}",
        )
        conn.execute(insert(s.contributions).values(period=period, occurred_at=t, amount_eur=amount, ledger_tx_id=tx.id))
        return True

    def withdraw(self, conn: Connection, t: dt.datetime, amount: Decimal, key: str,
                 source: Account = Account.CASH_RESERVE_PROFIT) -> int:
        if source == Account.CASH_RESERVE_TAX:
            raise InsufficientFunds("tax reserve cannot be withdrawn")
        tx = self.ledger.post(
            conn, occurred_at=t, recorded_at=t, kind="withdrawal", description="Profit withdrawal",
            postings=[Posting(Account.EQUITY_WITHDRAWALS, amount), Posting(source, -amount)],
            idempotency_key=f"withdrawal:{key}",
        )
        return tx.id

    def book_technology_cost(self, conn: Connection, t: dt.datetime, amount: Decimal, key: str) -> None:
        avail = self.spendable(conn)
        funding = split_funding(avail, amount, (Account.CASH_RECYCLED, Account.CASH_INVESTMENT))
        self.ledger.post(
            conn, occurred_at=t, recorded_at=t, kind="technology_cost", description="Infrastructure cost",
            postings=[Posting(Account.EXPENSE_TECHNOLOGY, amount)] + [Posting(a, -x) for a, x in funding],
            idempotency_key=f"tech:{key}",
        )

    # ----------------------------------------------------------------- queries
    def unsettled_proceeds(self, conn: Connection, at: dt.datetime) -> Decimal:
        """Sale cash booked to cash:recycled whose settlement (capital_available_at) is after `at`."""
        rows = conn.execute(select(s.executions.c.cash_eur).where(and_(
            s.executions.c.capital_available_at.is_not(None), s.executions.c.capital_available_at > ensure_utc(at),
            s.executions.c.cash_eur > 0)))
        return sum((r.cash_eur for r in rows), ZERO)

    def spendable(self, conn: Connection, at: dt.datetime | None = None) -> dict[Account, Decimal]:
        """Cash that can fund purchases at time `at`. Recycled proceeds count only once settled."""
        out = {a: self.ledger.balance(conn, a) for a in SPENDABLE_ACCOUNTS}
        if at is not None:
            pending = self.unsettled_proceeds(conn, at)
            out[Account.CASH_RECYCLED] = max(ZERO, out[Account.CASH_RECYCLED] - pending)
        return out

    def open_position(self, conn: Connection, token_id: int, book: str = OPERATIONAL):  # type: ignore[no-untyped-def]
        return conn.execute(
            select(s.positions).where(
                and_(s.positions.c.token_id == token_id, s.positions.c.book == book, s.positions.c.closed_at.is_(None))
            )
        ).first()

    def active_positions(self, conn: Connection, book: str = OPERATIONAL) -> list[Any]:
        return list(
            conn.execute(
                select(s.positions).where(and_(s.positions.c.book == book, s.positions.c.closed_at.is_(None)))
            )
        )

    # ----------------------------------------------------------------- trades
    def record_buy(
        self,
        conn: Connection,
        *,
        token_id: int,
        bucket: str,
        execution_id: int,
        fill: BuyFill,
        funding: list[tuple[Account, Decimal]],
        idempotency_key: str,
    ) -> int:
        t = ensure_utc(fill.executed_at)
        funded = sum((x for _, x in funding), ZERO)
        if funded != fill.cash_out_eur:
            raise InsufficientFunds(f"funding {funded} != cash out {fill.cash_out_eur}")
        for acct, _ in funding:
            if acct not in SPENDABLE_ACCOUNTS:
                raise InsufficientFunds(f"{acct} cannot fund purchases")
        pos = self.open_position(conn, token_id)
        if pos is None:
            prior = conn.execute(
                select(s.positions.c.id).where(and_(s.positions.c.token_id == token_id, s.positions.c.book == OPERATIONAL))
            ).all()
            pos_id = conn.execute(
                insert(s.positions).values(
                    token_id=token_id, book=OPERATIONAL, opened_at=t, status=PositionStatus.OPEN, bucket=bucket,
                    entry_number=len(prior) + 1, qty=fill.token_qty, cost_basis_eur=fill.cost_basis_eur,
                    deposit_eur=fill.deposit_eur, deposit_lamports=fill.deposit_lamports,
                    invested_eur_total=fill.cash_out_eur, realized_proceeds_eur=ZERO, realized_pnl_eur=ZERO,
                    peak_price_eur=fill.effective_price_eur, peak_multiple=ONE, capital_recovered=False,
                    partial_sells=0,
                )
            ).inserted_primary_key[0]
        else:  # INCREASE: add a new lot to the existing position
            pos_id = pos.id
            conn.execute(
                update(s.positions).where(s.positions.c.id == pos_id).values(
                    qty=pos.qty + fill.token_qty,
                    cost_basis_eur=pos.cost_basis_eur + fill.cost_basis_eur,
                    deposit_eur=pos.deposit_eur + fill.deposit_eur,
                    deposit_lamports=pos.deposit_lamports + fill.deposit_lamports,
                    invested_eur_total=pos.invested_eur_total + fill.cash_out_eur,
                )
            )
        conn.execute(
            insert(s.position_lots).values(
                position_id=pos_id, token_id=token_id, execution_id=execution_id, acquired_at=t,
                qty_initial=fill.token_qty, qty_remaining=fill.token_qty,
                cost_initial_eur=fill.cost_basis_eur, cost_remaining_eur=fill.cost_basis_eur,
            )
        )
        postings = [Posting(Account.POSITIONS, fill.cost_basis_eur, "lot at cost incl. acquisition fees")]
        if fill.deposit_eur > 0:
            postings.append(Posting(Account.DEPOSITS, fill.deposit_eur, "token account rent deposit"))
        postings += [Posting(a, -x) for a, x in funding]
        self.ledger.post(
            conn, occurred_at=t, recorded_at=t, kind="buy", description=f"Buy token {token_id}",
            postings=postings, idempotency_key=f"buy:{idempotency_key}", ref_type="execution", ref_id=execution_id,
        )
        return int(pos_id)

    def record_sell(
        self,
        conn: Connection,
        *,
        position_id: int,
        execution_id: int,
        fill: SellFill,
        idempotency_key: str,
    ) -> SaleResult:
        t = ensure_utc(fill.executed_at)
        pos = conn.execute(select(s.positions).where(s.positions.c.id == position_id)).first()
        if pos is None or pos.closed_at is not None:
            raise ValueError(f"position {position_id} not open")
        if fill.token_qty > pos.qty:
            raise ValueError(f"cannot sell {fill.token_qty} > held {pos.qty}")
        full = fill.token_qty == pos.qty
        if fill.close_account and not full:
            raise ValueError("token account can only be closed on full exit")
        if fill.deposit_refund_eur and not fill.close_account:
            raise ValueError("deposit refund requires closing the account")
        if fill.close_account and fill.deposit_refund_eur != pos.deposit_eur:
            raise ValueError("deposit refund must equal the recorded deposit")
        if fill.cash_in_eur < ZERO:
            raise InsufficientFunds("sale would have negative cash effect; reject upstream")
        if full and not fill.close_account and pos.deposit_eur > 0:
            raise ValueError("full exit must close the token account to release the deposit")

        lots = list(
            conn.execute(
                select(s.position_lots)
                .where(and_(s.position_lots.c.position_id == position_id, s.position_lots.c.qty_remaining > 0))
                .order_by(s.position_lots.c.acquired_at, s.position_lots.c.id)
            )
        )
        remaining = fill.token_qty
        total_cost = ZERO
        allocations: list[tuple[Any, Decimal, Decimal]] = []
        for lot in lots:
            if remaining == ZERO:
                break
            q = min(lot.qty_remaining, remaining)
            cost = lot.cost_remaining_eur if q == lot.qty_remaining else q_eur(lot.cost_remaining_eur * q / lot.qty_remaining)
            allocations.append((lot, q, cost))
            remaining -= q
            total_cost += cost
        if remaining != ZERO:
            raise ValueError("lots do not cover the sale quantity (reconciliation error)")
        if full:  # absorb any rounding residue so cost basis goes exactly to zero
            total_cost = pos.cost_basis_eur
            last_lot, last_q, last_cost = allocations[-1]
            allocations[-1] = (last_lot, last_q, last_cost + (pos.cost_basis_eur - sum(c for _, _, c in allocations)))

        net = fill.net_proceeds_eur
        gain = net - total_cost
        allocated_proceeds = ZERO
        for i, (lot, q, cost) in enumerate(allocations):
            proceeds = net - allocated_proceeds if i == len(allocations) - 1 else q_eur(net * q / fill.token_qty)
            allocated_proceeds += proceeds
            new_qty = lot.qty_remaining - q
            conn.execute(
                update(s.position_lots).where(s.position_lots.c.id == lot.id).values(
                    qty_remaining=new_qty,
                    cost_remaining_eur=ZERO if new_qty == ZERO else lot.cost_remaining_eur - cost,
                    closed_at=t if new_qty == ZERO else None,
                )
            )
            conn.execute(
                insert(s.lot_disposals).values(
                    lot_id=lot.id, execution_id=execution_id, disposed_at=t, qty=q, cost_basis_eur=cost,
                    proceeds_eur=proceeds, gain_eur=proceeds - cost,
                )
            )

        postings = [
            Posting(Account.POSITIONS, -total_cost, "cost of lots disposed (FIFO)"),
            Posting(Account.PNL_REALIZED, -gain, "realized gain (credit) / loss (debit)"),
        ]
        if net != ZERO:
            postings.append(Posting(Account.CASH_RECYCLED, net, "net sale proceeds"))
        if fill.close_account and pos.deposit_eur > 0:
            postings += [
                Posting(Account.CASH_RECYCLED, pos.deposit_eur, "rent deposit recovered"),
                Posting(Account.DEPOSITS, -pos.deposit_eur),
            ]
        # A burn/close with net < 0 is financed by the deposit refund in the same transaction.
        self.ledger.post(
            conn, occurred_at=t, recorded_at=t, kind="sell", description=f"Sell position {position_id}",
            postings=_merge(postings), idempotency_key=f"sell:{idempotency_key}", ref_type="execution",
            ref_id=execution_id,
        )

        new_qty = pos.qty - fill.token_qty
        new_cost = pos.cost_basis_eur - total_cost
        proceeds_total = pos.realized_proceeds_eur + net
        recovered = pos.capital_recovered or proceeds_total >= pos.invested_eur_total
        orig_qty = sum(
            (r.qty_initial for r in conn.execute(
                select(s.position_lots.c.qty_initial).where(s.position_lots.c.position_id == position_id))),
            ZERO,
        )
        if full:
            status, closed_at = PositionStatus.CLOSED, t
        elif new_qty <= orig_qty * self.cfg.exits.residual_min_fraction * 2:
            status, closed_at = PositionStatus.RESIDUAL, None
        elif recovered:
            status, closed_at = PositionStatus.CAPITAL_RECOVERED, None
        else:
            status, closed_at = PositionStatus.OPEN, None
        conn.execute(
            update(s.positions).where(s.positions.c.id == position_id).values(
                qty=new_qty, cost_basis_eur=new_cost, status=status, closed_at=closed_at,
                realized_proceeds_eur=proceeds_total, realized_pnl_eur=pos.realized_pnl_eur + gain,
                capital_recovered=recovered, partial_sells=pos.partial_sells + (0 if full else 1),
                capital_recovered_at=pos.capital_recovered_at or (t if recovered else None),
                deposit_eur=ZERO if fill.close_account else pos.deposit_eur,
                deposit_lamports=0 if fill.close_account else pos.deposit_lamports,
            )
        )
        if gain > ZERO:
            self._reserve_profit(conn, t, gain, key=idempotency_key)
        self.rebalance_tax_reserve(conn, t, key=idempotency_key)
        return SaleResult(position_id, fill.token_qty, total_cost, net, gain, fill.deposit_refund_eur
                          if fill.close_account else ZERO, closed_at is not None)

    def mark_dust(self, conn: Connection, position_id: int) -> None:
        conn.execute(update(s.positions).where(s.positions.c.id == position_id).values(status=PositionStatus.DUST))

    # ----------------------------------------------------------------- reserves & tax
    def _reserve_profit(self, conn: Connection, t: dt.datetime, gain: Decimal, key: str) -> None:
        amount = q_eur(gain * self.cfg.capital.profit_reserve_fraction)
        avail = self.ledger.balance(conn, Account.CASH_RECYCLED)
        amount = min(amount, avail)
        if amount > ZERO:
            self.ledger.post(
                conn, occurred_at=t, recorded_at=t, kind="reserve_profit", description="Profit reserve policy",
                postings=Ledger.transfer(Account.CASH_RECYCLED, Account.CASH_RESERVE_PROFIT, amount),
                idempotency_key=f"reserve_profit:{key}",
            )

    def realized_net_by_year(self, conn: Connection) -> dict[int, Decimal]:
        out: dict[int, Decimal] = {}
        for r in conn.execute(select(s.lot_disposals.c.disposed_at, s.lot_disposals.c.gain_eur)):
            y = r.disposed_at.year
            out[y] = out.get(y, ZERO) + r.gain_eur
        return out

    def estimated_tax_by_year(self, conn: Connection) -> dict[int, Decimal]:
        est = estimate_by_year(self.realized_net_by_year(conn), self.cfg.tax.brackets)
        return {y: q_eur(e.estimated_tax) for y, e in est.items()}

    def tax_paid_total(self, conn: Connection) -> Decimal:
        return self.ledger.balance(conn, Account.EXPENSE_TAX_PAID)

    def estimated_unpaid_tax(self, conn: Connection) -> Decimal:
        return max(ZERO, sum(self.estimated_tax_by_year(conn).values(), ZERO) - self.tax_paid_total(conn))

    def rebalance_tax_reserve(self, conn: Connection, t: dt.datetime, key: str) -> Decimal:
        """Move cash between recycled and tax reserve so reserve ~= coverage * unpaid estimated tax."""
        target = q_eur(self.estimated_unpaid_tax(conn) * self.cfg.capital.tax_reserve_coverage)
        current = self.ledger.balance(conn, Account.CASH_RESERVE_TAX)
        diff = target - current
        if diff > ZERO:
            take = min(diff, self.ledger.balance(conn, Account.CASH_RECYCLED))
            if take > ZERO:
                self.ledger.post(
                    conn, occurred_at=t, recorded_at=t, kind="reserve_tax", description="Tax provision top-up",
                    postings=Ledger.transfer(Account.CASH_RECYCLED, Account.CASH_RESERVE_TAX, take),
                    idempotency_key=f"reserve_tax_up:{key}",
                )
        elif diff < ZERO:
            self.ledger.post(
                conn, occurred_at=t, recorded_at=t, kind="reserve_tax", description="Tax provision release",
                postings=Ledger.transfer(Account.CASH_RESERVE_TAX, Account.CASH_RECYCLED, -diff),
                idempotency_key=f"reserve_tax_down:{key}",
            )
        return target

    # ----------------------------------------------------------------- reconciliation
    def reconcile(self, conn: Connection) -> ReconciliationReport:
        rep = ReconciliationReport(ok=True)
        bal = self.ledger.balances(conn)
        rep.add("trial_balance_zero", sum(bal.values(), ZERO) == ZERO, total=sum(bal.values(), ZERO))
        for acct in CASH_ACCOUNTS:
            rep.add(f"non_negative:{acct}", bal[acct] >= ZERO, balance=bal[acct])
        # every transaction balances individually
        sums: dict[int, Decimal] = {}
        for r in conn.execute(select(s.ledger_postings.c.transaction_id, s.ledger_postings.c.amount_eur)):
            sums[r.transaction_id] = sums.get(r.transaction_id, ZERO) + r.amount_eur
        bad = [k for k, v in sums.items() if v != ZERO]
        rep.add("each_transaction_balanced", not bad, unbalanced=len(bad))

        open_pos = self.active_positions(conn)
        lots = list(conn.execute(select(s.position_lots).where(s.position_lots.c.qty_remaining > 0)))
        lots_cost = sum((lot.cost_remaining_eur for lot in lots), ZERO)
        pos_cost = sum((p.cost_basis_eur for p in open_pos), ZERO)
        rep.add("positions_vs_lots_cost", lots_cost == bal[Account.POSITIONS] == pos_cost,
                ledger=bal[Account.POSITIONS], lots=lots_cost, positions=pos_cost)
        by_pos: dict[int, Decimal] = {}
        for lot in lots:
            by_pos[lot.position_id] = by_pos.get(lot.position_id, ZERO) + lot.qty_remaining
        qty_bad = [p.id for p in open_pos if by_pos.get(p.id, ZERO) != p.qty]
        rep.add("position_qty_vs_lots", not qty_bad, mismatched=len(qty_bad))
        dep = sum((p.deposit_eur for p in open_pos), ZERO)
        rep.add("deposits_vs_positions", dep == bal[Account.DEPOSITS], ledger=bal[Account.DEPOSITS], positions=dep)
        contrib = sum((r.amount_eur for r in conn.execute(select(s.contributions.c.amount_eur))), ZERO)
        rep.add("contributions_vs_equity", contrib == -bal[Account.EQUITY_CONTRIBUTIONS],
                ledger=-bal[Account.EQUITY_CONTRIBUTIONS], table=contrib)
        gains = sum((r.gain_eur for r in conn.execute(select(s.lot_disposals.c.gain_eur))), ZERO)
        rep.add("realized_vs_disposals", gains == -bal[Account.PNL_REALIZED],
                ledger=-bal[Account.PNL_REALIZED], disposals=gains)
        # Fundamental identity: contributed - withdrawn + realized - expenses == cash + positions + deposits
        lhs = (-bal[Account.EQUITY_CONTRIBUTIONS] - bal[Account.EQUITY_WITHDRAWALS] - bal[Account.PNL_REALIZED]
               - bal[Account.EXPENSE_NETWORK] - bal[Account.EXPENSE_TECHNOLOGY] - bal[Account.EXPENSE_TAX_PAID])
        rhs = sum((bal[a] for a in CASH_ACCOUNTS), ZERO) + bal[Account.POSITIONS] + bal[Account.DEPOSITS]
        rep.add("capital_identity", lhs == rhs, sources=lhs, uses=rhs)
        return rep

    # ----------------------------------------------------------------- valuation & snapshots
    def state(self, conn: Connection, t: dt.datetime, valuations: dict[int, PositionValuation]) -> PortfolioState:
        t = ensure_utc(t)
        bal = self.ledger.balances(conn)
        open_pos = self.active_positions(conn)
        mark = liq = ZERO
        stale = unvalued = 0
        for p in open_pos:
            v = valuations.get(p.id)
            if v is None or v.liquidation_eur is None:
                unvalued += 1
                continue
            if v.stale:
                stale += 1
            liq += v.liquidation_eur
            mark += v.mark_eur if v.mark_eur is not None else ZERO
        cash = {str(a): bal[a] for a in CASH_ACCOUNTS}
        est_tax_year = self.estimated_tax_by_year(conn)
        unpaid_tax = self.estimated_unpaid_tax(conn)
        net_worth = sum(cash.values(), ZERO) + bal[Account.DEPOSITS] + liq - unpaid_tax
        contributions = -bal[Account.EQUITY_CONTRIBUTIONS]
        withdrawals = bal[Account.EQUITY_WITHDRAWALS]
        costs = self._costs_total(conn) + bal[Account.EXPENSE_NETWORK] + bal[Account.EXPENSE_TECHNOLOGY]
        return PortfolioState(
            as_of=t, cash=cash, deposits=bal[Account.DEPOSITS], positions_cost=bal[Account.POSITIONS],
            positions_mark=mark, positions_liquidation=liq, open_positions=len(open_pos), stale_positions=stale,
            unvalued_positions=unvalued, contributions_total=contributions, withdrawals_total=withdrawals,
            realized_pnl_total=-bal[Account.PNL_REALIZED], costs_total=costs,
            est_tax_realized_ytd=est_tax_year.get(t.year, ZERO), net_worth_liquidable=net_worth,
            objective=net_worth + withdrawals - contributions, reconciliation=self.reconcile(conn),
        )

    def _costs_total(self, conn: Connection) -> Decimal:
        """Explicit fees paid (network + priority + embedded pool/aggregator fees) across executions."""
        total = ZERO
        for r in conn.execute(select(s.executions.c.network_fee_eur, s.executions.c.priority_fee_eur,
                                     s.executions.c.pool_fee_eur, s.executions.c.aggregator_fee_eur)):
            total += r.network_fee_eur + r.priority_fee_eur + r.pool_fee_eur + r.aggregator_fee_eur
        return total

    def persist_snapshot(self, conn: Connection, st: PortfolioState, details: dict[str, Any] | None = None) -> int:
        prev = conn.execute(
            select(s.portfolio_snapshots.c.contributions_total_eur, s.portfolio_snapshots.c.withdrawals_total_eur)
            .where(s.portfolio_snapshots.c.book == OPERATIONAL)
            .order_by(s.portfolio_snapshots.c.as_of.desc(), s.portfolio_snapshots.c.id.desc()).limit(1)
        ).first()
        flow = st.contributions_total - st.withdrawals_total
        if prev is not None:
            flow -= prev.contributions_total_eur - prev.withdrawals_total_eur
        return int(conn.execute(
            insert(s.portfolio_snapshots).values(
                as_of=st.as_of, book=OPERATIONAL,
                cash_investment_eur=st.cash[str(Account.CASH_INVESTMENT)],
                cash_recycled_eur=st.cash[str(Account.CASH_RECYCLED)],
                cash_reserve_eur=st.cash[str(Account.CASH_RESERVE_PROFIT)] + st.cash[str(Account.CASH_RESERVE_TAX)],
                deposits_eur=st.deposits, positions_cost_eur=st.positions_cost, positions_mark_eur=st.positions_mark,
                positions_liquidation_eur=st.positions_liquidation, open_positions=st.open_positions,
                stale_positions=st.stale_positions, unvalued_positions=st.unvalued_positions,
                contributions_total_eur=st.contributions_total, withdrawals_total_eur=st.withdrawals_total,
                external_flow_since_last_eur=flow, realized_pnl_total_eur=st.realized_pnl_total,
                costs_total_eur=st.costs_total, est_tax_realized_ytd_eur=st.est_tax_realized_ytd,
                net_worth_liquidable_eur=st.net_worth_liquidable, objective_eur=st.objective,
                reconciliation_ok=st.reconciliation.ok,
                details={"reconciliation": st.reconciliation.checks, **(details or {})},
            )
        ).inserted_primary_key[0])


def _merge(postings: list[Posting]) -> list[Posting]:
    acc: dict[Account, Decimal] = {}
    for p in postings:
        acc[p.account] = acc.get(p.account, ZERO) + p.amount
    return [Posting(a, x) for a, x in acc.items() if x != ZERO]
