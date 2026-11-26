from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import insert

from aegis.config import StrategyConfig
from aegis.core.clock import UTC
from aegis.execution.types import BuyFill, SellFill
from aegis.storage import schema as s
from aegis.storage.db import create_all, make_engine

FIXTURES = Path(__file__).parent / "fixtures" / "recorded_2026-11-26"
T0 = dt.datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def engine():
    eng = make_engine("sqlite://")
    create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def cfg() -> StrategyConfig:
    return StrategyConfig()


_addr_counter = [0]
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(n: int, width: int = 32) -> str:
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return ("T" + out).ljust(width, "1")


def new_token(conn, address: str | None = None, t: dt.datetime = T0) -> int:
    if address is None:
        _addr_counter[0] += 1
        address = b58(_addr_counter[0])
    return int(conn.execute(insert(s.tokens).values(
        network="solana", address=address, symbol="TST", name="Test", decimals=6,
        first_seen_at=t, last_seen_at=t, universes=[], meta={}, market_state="ELIGIBLE", market_state_since=t,
        explosion_count=0, entries=0,
    )).inserted_primary_key[0])


def new_execution(conn, token_id: int, side: str, t: dt.datetime, key: str) -> int:
    oid = conn.execute(insert(s.orders).values(
        idempotency_key=key, token_id=token_id, side=side, mode="PAPER_TRADING", requested_at=t, status="FILLED",
    )).inserted_primary_key[0]
    return int(conn.execute(insert(s.executions).values(
        order_id=oid, executed_at=t, method="cpmm_estimate", token_qty=Decimal(1), cash_eur=Decimal(0),
        swap_amount_eur=Decimal(0), pool_fee_eur=Decimal(0), aggregator_fee_eur=Decimal(0),
        network_fee_eur=Decimal(0), priority_fee_eur=Decimal(0), deposit_eur=Decimal(0), sol_eur=Decimal(64),
        details={},
    )).inserted_primary_key[0])


def buy_fill(t=T0, qty="1000", swap="0.88", net="0.0004", prio="0.0064", dep="0.0955", lamports=1488440):
    return BuyFill(executed_at=t, token_qty=Decimal(qty), swap_input_eur=Decimal(swap),
                   network_fee_eur=Decimal(net), priority_fee_eur=Decimal(prio), deposit_eur=Decimal(dep),
                   deposit_lamports=lamports, reference_price_eur=None, price_impact_pct=None)


def sell_fill(t, qty, out, net="0.0004", prio="0.0064", refund="0", close=False):
    return SellFill(executed_at=t, token_qty=Decimal(qty), swap_output_eur=Decimal(out),
                    network_fee_eur=Decimal(net), priority_fee_eur=Decimal(prio),
                    deposit_refund_eur=Decimal(refund), reference_price_eur=None, price_impact_pct=None,
                    close_account=close)
