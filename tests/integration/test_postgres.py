"""PostgreSQL compatibility: Alembic migration + worker cycles + exact decimals + partial unique index.

Runs only when AEGIS_TEST_POSTGRES_URL points to a disposable database (CI provides one)."""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from aegis.config import ExecutionMode, RiskLimits, Settings
from aegis.core.clock import ManualClock
from aegis.research.simmarket import TOKENS, frozen_mint, make_script
from aegis.storage import schema as s
from aegis.storage.db import make_engine, migrate
from aegis.worker.gateway import ScriptedGateway
from aegis.worker.runner import Worker
from tests.integration.test_worker_cycle import START, _cfg

URL = os.environ.get("AEGIS_TEST_POSTGRES_URL")
pytestmark = [pytest.mark.postgres, pytest.mark.skipif(not URL, reason="AEGIS_TEST_POSTGRES_URL not set")]


def test_migration_and_cycles_on_postgres():
    eng = make_engine(URL)
    with eng.begin() as c:  # disposable DB: reset
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    migrate(eng)
    clock = ManualClock(START)
    gw = ScriptedGateway(script=make_script(START), clock=clock.now, unsellable={TOKENS["honeypot"]},
                         mint_overrides={TOKENS["frozen"]: frozen_mint(START)})
    w = Worker(eng, Settings(database_url=URL, execution_mode=ExecutionMode.PAPER_TRADING), _cfg(), RiskLimits(), gw, clock)
    w.bootstrap()
    for _ in range(24):
        rep = w.run_cycle()
        assert rep.reconciliation_ok
        clock.advance(minutes=30)
    with eng.connect() as c:
        amounts = [r.amount_eur for r in c.execute(select(s.ledger_postings.c.amount_eur))]
        assert amounts and all(isinstance(a, Decimal) for a in amounts)
        assert sum(amounts, Decimal(0)) == 0
        assert w.portfolio.reconcile(c).ok
        idx = c.execute(text("select indexdef from pg_indexes where indexname='uq_positions_open_token'")).scalar_one()
        assert "WHERE (closed_at IS NULL)" in idx
