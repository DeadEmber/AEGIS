"""Simulated Execution Engine (HISTORICAL_BACKTEST / PAPER_TRADING / SHADOW_TRADING).

Flow per order: mode guard -> idempotency -> pre-trade validation -> price from an executable
quote if fresh (else constant-product estimate, flagged) -> conservative adverse adjustment ->
persist order + execution -> book into the ledger (paper/backtest only).

A simulated sale is never "executed" merely because a candle printed a price: it needs either an
executable quote or the conservative reserve-based estimate at the time of decision, and pays all
costs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from aegis.accounting.ledger import Account, InsufficientFunds, split_funding
from aegis.accounting.portfolio import PortfolioService, SaleResult
from aegis.config import ExecutionMode, StrategyConfig
from aegis.core.assets import WSOL_MINT
from aegis.core.clock import Clock, ensure_utc
from aegis.core.money import LAMPORTS_PER_SOL, ONE, ZERO, from_raw_units, q_eur, q_qty, sol_to_lamports
from aegis.execution.costs import CostModel
from aegis.execution.modes import assert_simulated, books_to_operational_ledger
from aegis.execution.types import BuyFill, FillMethod, MarketContext, SellFill, SwapQuote
from aegis.storage import schema as s

ENGINE_VERSION = "exec-sim-0.2"


@dataclass(frozen=True)
class ExecutionResult:
    status: str  # FILLED | REJECTED | EXPIRED | DUPLICATE | SHADOW
    order_id: int | None
    reason: str = ""
    execution_id: int | None = None
    position_id: int | None = None
    buy: BuyFill | None = None
    sell: SellFill | None = None
    sale: SaleResult | None = None

    @property
    def filled(self) -> bool:
        return self.status == "FILLED"


@dataclass(frozen=True)
class TokenRef:
    id: int
    address: str
    decimals: int | None


class SimulatedExecutionEngine:
    def __init__(self, strategy: StrategyConfig, mode: ExecutionMode, portfolio: PortfolioService, clock: Clock,
                 *, max_staleness_minutes: int = 30, paper_adverse_bps: int = 100):
        assert_simulated(mode)
        self.cfg = strategy
        self.mode = mode
        self.portfolio = portfolio
        self.clock = clock
        self.costs = CostModel(strategy.execution)
        self.max_staleness = dt.timedelta(minutes=max_staleness_minutes)
        self.adverse = Decimal(paper_adverse_bps) / Decimal(10_000)

    # ------------------------------------------------------------------ helpers
    def _existing(self, conn: Connection, key: str) -> ExecutionResult | None:
        row = conn.execute(select(s.orders).where(s.orders.c.idempotency_key == key)).first()
        if row is None:
            return None
        ex = conn.execute(select(s.executions.c.id).where(s.executions.c.order_id == row.id)).first()
        return ExecutionResult("DUPLICATE", row.id, f"order already exists with status {row.status}",
                               execution_id=ex.id if ex else None)

    def _order(self, conn: Connection, *, key: str, token: TokenRef, side: str, decision_id: int | None,
               notional: Decimal | None, qty: Decimal | None, quote: SwapQuote | None) -> int:
        expires = None
        if quote is not None:
            expires = quote.fetched_at + dt.timedelta(seconds=self.cfg.execution.quote_ttl_seconds)
        return int(conn.execute(insert(s.orders).values(
            idempotency_key=key, decision_id=decision_id, token_id=token.id, side=side, mode=str(self.mode),
            requested_at=self.clock.now(), notional_eur=notional, token_qty=qty,
            quote=None if quote is None else _quote_json(quote), quote_expires_at=expires, status="PENDING",
        )).inserted_primary_key[0])

    def _finish(self, conn: Connection, order_id: int, status: str, reason: str = "") -> None:
        conn.execute(update(s.orders).where(s.orders.c.id == order_id).values(status=status, reject_reason=reason or None))

    def _reject(self, conn: Connection, order_id: int, reason: str, status: str = "REJECTED") -> ExecutionResult:
        self._finish(conn, order_id, status, reason)
        return ExecutionResult(status, order_id, reason)

    def _stale(self, market: MarketContext, at: dt.datetime) -> bool:
        return at - ensure_utc(market.observed_at) > self.max_staleness

    def _quote_usable(self, quote: SwapQuote | None, at: dt.datetime, *, input_mint: str, output_mint: str,
                      in_amount: int) -> tuple[bool, str]:
        if quote is None:
            return False, "no_quote"
        if quote.input_mint != input_mint or quote.output_mint != output_mint:
            return False, "quote_pair_mismatch"
        if quote.fetched_at + dt.timedelta(seconds=self.cfg.execution.quote_ttl_seconds) < at:
            return False, "quote_expired"
        if in_amount <= 0 or abs(quote.in_amount_raw - in_amount) > max(1, in_amount // 100):
            return False, "quote_amount_mismatch"
        if quote.out_amount_raw <= 0:
            return False, "quote_zero_output"
        return True, ""

    def _execution_row(self, conn: Connection, order_id: int, *, at: dt.datetime, method: FillMethod, qty: Decimal,
                       cash: Decimal, swap: Decimal, eff_price: Decimal | None, ref_price: Decimal | None,
                       impact: Decimal | None, pool_fee: Decimal, agg_fee: Decimal, net_fee: Decimal,
                       prio_fee: Decimal, deposit: Decimal, sol_eur: Decimal, details: dict[str, Any]) -> int:
        return int(conn.execute(insert(s.executions).values(
            order_id=order_id, executed_at=at, method=str(method), token_qty=qty, cash_eur=cash, swap_amount_eur=swap,
            price_eur_effective=None if eff_price is None else q_eur(eff_price),
            reference_price_eur=None if ref_price is None else q_eur(ref_price),
            price_impact_pct=None if impact is None else q_eur(impact), pool_fee_eur=q_eur(pool_fee),
            aggregator_fee_eur=q_eur(agg_fee), network_fee_eur=net_fee, priority_fee_eur=prio_fee, deposit_eur=deposit,
            sol_eur=sol_eur, latency_ms=self.cfg.execution.simulated_latency_seconds * 1000,
            details={"engine": ENGINE_VERSION, **details},
        )).inserted_primary_key[0])

    # ------------------------------------------------------------------ BUY
    def buy(self, conn: Connection, *, token: TokenRef, bucket: str, ticket_eur: Decimal, market: MarketContext,
            idempotency_key: str, decision_id: int | None = None, quote: SwapQuote | None = None,
            rent_lamports: int | None = None) -> ExecutionResult:
        assert_simulated(self.mode)
        if (dup := self._existing(conn, idempotency_key)) is not None:
            return dup
        ex_cfg = self.cfg.execution
        order_id = self._order(conn, key=idempotency_key, token=token, side="BUY", decision_id=decision_id,
                               notional=ticket_eur, qty=None, quote=quote)
        at = self.clock.now() + dt.timedelta(seconds=ex_cfg.simulated_latency_seconds)
        if token.decimals is None:
            return self._reject(conn, order_id, "token_decimals_unknown")
        if self._stale(market, self.clock.now()):
            return self._reject(conn, order_id, "stale_market_data")
        if market.sol_eur <= 0:
            return self._reject(conn, order_id, "sol_eur_unavailable")
        new_account = self.portfolio.open_position(conn, token.id) is None
        net = self.costs.network_costs(market.sol_eur, new_account=new_account, rent_lamports=rent_lamports)
        swap_in = q_eur(ticket_eur - net.deposit - net.unrecoverable) if self.cfg.capital.ticket_includes_costs \
            else q_eur(ticket_eur)
        if swap_in <= 0:
            return self._reject(conn, order_id, "costs_exceed_ticket")
        in_lamports = sol_to_lamports(swap_in / market.sol_eur)
        ok, why = self._quote_usable(quote, at, input_mint=WSOL_MINT, output_mint=token.address, in_amount=in_lamports)
        details: dict[str, Any] = {"bucket": bucket, "quote_status": why or "used"}
        if quote is not None and not ok and why == "quote_expired":
            return self._reject(conn, order_id, "quote_expired", status="EXPIRED")
        if ok and ex_cfg.prefer_executable_quotes:
            assert quote is not None
            out_raw = max(quote.min_out_raw, int(Decimal(quote.out_amount_raw) * (ONE - self.adverse)))
            qty = q_qty(from_raw_units(out_raw, token.decimals), token.decimals)
            impact = quote.price_impact_pct
            agg_fee = q_eur(swap_in * Decimal(quote.platform_fee_bps) / Decimal(10_000))
            pool_fee = ZERO  # embedded in the quote; unknown split
            method = FillMethod.JUPITER_QUOTE
            details["route"] = list(quote.route_labels)
        else:
            if market.price_eur is None or market.liquidity_eur is None or market.price_eur <= 0:
                return self._reject(conn, order_id, "no_quote_and_no_reserves")
            if market.pool_type == "bonding_curve":
                return self._reject(conn, order_id, "cpmm_invalid_for_bonding_curve")
            raw_qty, cp = self.costs.estimate_buy(swap_in, market.price_eur, market.liquidity_eur)
            qty = q_qty(raw_qty * (ONE - self.adverse), token.decimals)
            impact = cp.price_impact_pct
            pool_fee = cp.pool_fee
            agg_fee = ZERO
            method = FillMethod.CPMM_ESTIMATE
        if qty <= 0:
            return self._reject(conn, order_id, "zero_output")
        if impact is not None and impact > ex_cfg.max_price_impact_pct:
            return self._reject(conn, order_id, f"price_impact_{impact:.2f}pct_exceeds_limit")
        # Unrecoverable round-trip cost estimate vs ticket (deposit excluded: recoverable).
        impact_frac = (impact or ZERO) / 100
        fee_frac = Decimal(ex_cfg.estimated_pool_fee_bps + ex_cfg.aggregator_fee_bps) / Decimal(10_000)
        rt_cost = 2 * net.unrecoverable + swap_in * (2 * impact_frac + 2 * fee_frac + self.adverse * 2)
        cost_pct = rt_cost / ticket_eur * 100
        details["roundtrip_cost_pct_est"] = str(q_eur(cost_pct))
        if cost_pct > ex_cfg.max_cost_to_ticket_pct:
            return self._reject(conn, order_id, f"roundtrip_cost_{cost_pct:.1f}pct_exceeds_limit")
        fill = BuyFill(
            executed_at=at, token_qty=qty, swap_input_eur=swap_in, network_fee_eur=net.base_fee,
            priority_fee_eur=net.priority_fee, deposit_eur=net.deposit, deposit_lamports=net.deposit_lamports,
            reference_price_eur=market.price_eur, price_impact_pct=impact, pool_fee_eur=pool_fee,
            aggregator_fee_eur=agg_fee, sol_eur=market.sol_eur, method=method, details=details,
        )
        if not books_to_operational_ledger(self.mode):
            self._finish(conn, order_id, "SHADOW")
            return ExecutionResult("SHADOW", order_id, "shadow mode: not booked", buy=fill)
        order = (Account.CASH_RECYCLED, Account.CASH_INVESTMENT) if self.cfg.capital.buy_from_recycled_first \
            else (Account.CASH_INVESTMENT, Account.CASH_RECYCLED)
        try:
            funding = split_funding(self.portfolio.spendable(conn), fill.cash_out_eur, order)
        except InsufficientFunds:
            return self._reject(conn, order_id, "insufficient_spendable_cash")
        ex_id = self._execution_row(
            conn, order_id, at=at, method=method, qty=qty, cash=-fill.cash_out_eur, swap=swap_in,
            eff_price=fill.effective_price_eur, ref_price=market.price_eur, impact=impact, pool_fee=pool_fee,
            agg_fee=agg_fee, net_fee=net.base_fee, prio_fee=net.priority_fee, deposit=net.deposit,
            sol_eur=market.sol_eur, details=details,
        )
        pos_id = self.portfolio.record_buy(conn, token_id=token.id, bucket=bucket, execution_id=ex_id, fill=fill,
                                           funding=funding, idempotency_key=idempotency_key)
        self._finish(conn, order_id, "FILLED")
        return ExecutionResult("FILLED", order_id, execution_id=ex_id, position_id=pos_id, buy=fill)

    # ------------------------------------------------------------------ SELL
    def sell(self, conn: Connection, *, token: TokenRef, position_id: int, fraction: Decimal, market: MarketContext,
             idempotency_key: str, decision_id: int | None = None, quote: SwapQuote | None = None) -> ExecutionResult:
        assert_simulated(self.mode)
        if (dup := self._existing(conn, idempotency_key)) is not None:
            return dup
        pos = conn.execute(select(s.positions).where(s.positions.c.id == position_id)).first()
        if pos is None or pos.closed_at is not None:
            raise ValueError("position not open")
        ex_cfg = self.cfg.execution
        full = fraction >= ONE
        qty = pos.qty if full else q_qty(pos.qty * fraction, token.decimals)
        if not full and qty >= pos.qty:
            full, qty = True, pos.qty
        order_id = self._order(conn, key=idempotency_key, token=token, side="SELL", decision_id=decision_id,
                               notional=None, qty=qty, quote=quote)
        if qty <= 0:
            return self._reject(conn, order_id, "zero_quantity")
        at = self.clock.now() + dt.timedelta(seconds=ex_cfg.simulated_latency_seconds)
        if self._stale(market, self.clock.now()):
            return self._reject(conn, order_id, "stale_market_data")
        decimals = token.decimals if token.decimals is not None else 9
        in_raw = int(qty * (Decimal(10) ** decimals))
        ok, why = self._quote_usable(quote, at, input_mint=token.address, output_mint=WSOL_MINT, in_amount=in_raw)
        details: dict[str, Any] = {"fraction": str(fraction), "quote_status": why or "used"}
        if quote is not None and not ok and why == "quote_expired":
            return self._reject(conn, order_id, "quote_expired", status="EXPIRED")
        if ok and ex_cfg.prefer_executable_quotes:
            assert quote is not None
            out_raw = max(quote.min_out_raw, int(Decimal(quote.out_amount_raw) * (ONE - self.adverse)))
            gross = q_eur(Decimal(out_raw) / LAMPORTS_PER_SOL * market.sol_eur)
            impact = quote.price_impact_pct
            agg_fee = q_eur(gross * Decimal(quote.platform_fee_bps) / Decimal(10_000))
            pool_fee = ZERO
            method = FillMethod.JUPITER_QUOTE
            details["route"] = list(quote.route_labels)
        else:
            if market.price_eur is None or market.liquidity_eur is None or market.price_eur <= 0:
                return self._reject(conn, order_id, "no_quote_and_no_reserves")
            cp = self.costs.estimate_sell(qty, market.price_eur, market.liquidity_eur)
            gross = q_eur(cp.out_amount * (ONE - self.adverse))
            impact, pool_fee, agg_fee, method = cp.price_impact_pct, cp.pool_fee, ZERO, FillMethod.CPMM_ESTIMATE
        net = self.costs.network_costs(market.sol_eur, new_account=False, rent_lamports=0)
        refund = pos.deposit_eur if full else ZERO
        fill = SellFill(
            executed_at=at, token_qty=qty, swap_output_eur=gross, network_fee_eur=net.base_fee,
            priority_fee_eur=net.priority_fee, deposit_refund_eur=refund, reference_price_eur=market.price_eur,
            price_impact_pct=impact, close_account=full, pool_fee_eur=pool_fee, aggregator_fee_eur=agg_fee,
            sol_eur=market.sol_eur, method=method, details=details,
        )
        if fill.net_proceeds_eur < self.cfg.exits.min_sale_eur:
            return self._reject(conn, order_id, "proceeds_below_minimum_sale")
        if not books_to_operational_ledger(self.mode):
            self._finish(conn, order_id, "SHADOW")
            return ExecutionResult("SHADOW", order_id, "shadow mode: not booked", sell=fill)
        ex_id = self._execution_row(
            conn, order_id, at=at, method=method, qty=qty, cash=fill.cash_in_eur, swap=gross,
            eff_price=gross / qty, ref_price=market.price_eur, impact=impact, pool_fee=pool_fee, agg_fee=agg_fee,
            net_fee=net.base_fee, prio_fee=net.priority_fee, deposit=refund, sol_eur=market.sol_eur, details=details,
        )
        sale = self.portfolio.record_sell(conn, position_id=position_id, execution_id=ex_id, fill=fill,
                                          idempotency_key=idempotency_key)
        self._finish(conn, order_id, "FILLED")
        return ExecutionResult("FILLED", order_id, execution_id=ex_id, position_id=position_id, sell=fill, sale=sale)

    # ------------------------------------------------------------------ BURN & CLOSE (dead positions)
    def burn_close(self, conn: Connection, *, token: TokenRef, position_id: int, sol_eur: Decimal,
                   idempotency_key: str, decision_id: int | None = None) -> ExecutionResult:
        """Write off worthless tokens and recover the token-account rent deposit.

        Economically rational when deposit > close fee and the position cannot be sold above costs."""
        assert_simulated(self.mode)
        if (dup := self._existing(conn, idempotency_key)) is not None:
            return dup
        pos = conn.execute(select(s.positions).where(s.positions.c.id == position_id)).first()
        if pos is None or pos.closed_at is not None:
            raise ValueError("position not open")
        order_id = self._order(conn, key=idempotency_key, token=token, side="SELL", decision_id=decision_id,
                               notional=None, qty=pos.qty, quote=None)
        fee = self.costs.close_account_cost(sol_eur)
        if pos.deposit_eur <= fee:
            return self._reject(conn, order_id, "deposit_does_not_cover_close_fee")
        at = self.clock.now() + dt.timedelta(seconds=self.cfg.execution.simulated_latency_seconds)
        fill = SellFill(executed_at=at, token_qty=pos.qty, swap_output_eur=ZERO, network_fee_eur=fee,
                        priority_fee_eur=ZERO, deposit_refund_eur=pos.deposit_eur, reference_price_eur=None,
                        price_impact_pct=None, close_account=True, sol_eur=sol_eur, method=FillMethod.BURN_CLOSE)
        if not books_to_operational_ledger(self.mode):
            self._finish(conn, order_id, "SHADOW")
            return ExecutionResult("SHADOW", order_id, sell=fill)
        ex_id = self._execution_row(
            conn, order_id, at=at, method=FillMethod.BURN_CLOSE, qty=pos.qty, cash=fill.cash_in_eur, swap=ZERO,
            eff_price=ZERO, ref_price=None, impact=None, pool_fee=ZERO, agg_fee=ZERO, net_fee=fee, prio_fee=ZERO,
            deposit=pos.deposit_eur, sol_eur=sol_eur, details={"reason": "write_off_and_recover_rent"},
        )
        sale = self.portfolio.record_sell(conn, position_id=position_id, execution_id=ex_id, fill=fill,
                                          idempotency_key=idempotency_key)
        self._finish(conn, order_id, "FILLED")
        return ExecutionResult("FILLED", order_id, execution_id=ex_id, position_id=position_id, sell=fill, sale=sale)


def _quote_json(q: SwapQuote) -> dict[str, Any]:
    return {
        "source": q.source, "input_mint": q.input_mint, "output_mint": q.output_mint,
        "in_amount_raw": str(q.in_amount_raw), "out_amount_raw": str(q.out_amount_raw),
        "min_out_raw": str(q.min_out_raw), "price_impact_pct": None if q.price_impact_pct is None else str(q.price_impact_pct),
        "route": list(q.route_labels), "fetched_at": q.fetched_at.isoformat(), "context_slot": q.context_slot,
        "platform_fee_bps": q.platform_fee_bps,
    }
