"""Phase 2 block A: the UNCHANGED production worker replays a historical store through HistoricalGateway."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from aegis.config import ExecutionMode, RiskLimits, Settings, StrategyConfig
from aegis.core.clock import ManualClock
from aegis.history.gateway import HistoricalGateway
from aegis.storage import repo
from aegis.storage import schema as s
from aegis.storage.db import create_all, make_engine
from aegis.worker.runner import Worker
from tests.history_synth import T0, make_store


def _cfg_young() -> StrategyConfig:
    """Production config except the 24 h age floor (documented research variant); 24 h counts are present."""
    cfg = StrategyConfig()
    cfg.discovery.min_token_age_hours = Decimal(0)
    cfg.discovery.min_txns_24h = 0
    cfg.discovery.min_unique_traders_24h = 0
    cfg.discovery.max_price_eur = None
    return cfg


def _store_with_24h_counts():
    st = make_store(2, minutes=180)
    for tr in st.tokens.values():
        for k, o in enumerate(tr.obs):
            o.buys_h24, o.sells_h24, o.buyers_h24, o.sellers_h24 = 60 + k, 20 + k, 40, 15
            o.vol = (o.vol[0], o.vol[1], None, 50_000.0)
    return st


def _replay(tmp_path, *, invariants: bool, hours: float = 3.0, cfg: StrategyConfig | None = None):
    st = _store_with_24h_counts()
    clock = ManualClock(dt.datetime.fromtimestamp(T0, dt.UTC))
    eng = make_engine(f"sqlite:///{tmp_path}/h.sqlite")
    create_all(eng)
    gw = HistoricalGateway(st, clock.now, launchpad_invariants=invariants)
    w = Worker(eng, Settings(execution_mode=ExecutionMode.HISTORICAL_BACKTEST), cfg or _cfg_young(), RiskLimits(), gw, clock)
    w.bootstrap()
    rep = w.replay(clock.now() + dt.timedelta(hours=hours), full_interval_s=300, data_resolution_s=60)
    return w, eng, rep


def test_unknown_historical_security_blocks_every_purchase(tmp_path):
    _, eng, rep = _replay(tmp_path, invariants=False)
    with eng.connect() as c:
        assert c.execute(select(func.count()).select_from(s.positions)).scalar() == 0
        assert repo.get_flag(c, "data_mode") == "historical"
    assert rep.always_reconciled


def test_launchpad_invariants_allow_buys_with_capped_evidence(tmp_path):
    _, eng, rep = _replay(tmp_path, invariants=True)
    with eng.connect() as c:
        assert c.execute(select(func.count()).select_from(s.positions)).scalar() > 0
        findings = [f for row in c.execute(select(s.security_assessments.c.findings)) for f in row.findings]
    assert findings and not any(f["evidence"] == "CONFIRMED" for f in findings)  # assumptions never CONFIRMED
    assert rep.always_reconciled


def test_historical_database_is_never_mixed_with_simulated_data(tmp_path):
    w, eng, _ = _replay(tmp_path, invariants=False, hours=0.2)
    from aegis.research.simmarket import make_script
    from aegis.worker.gateway import ScriptedGateway
    clock = ManualClock(dt.datetime.fromtimestamp(T0, dt.UTC))
    sim = ScriptedGateway(script=make_script(clock.now()), clock=clock.now)
    w2 = Worker(eng, Settings(execution_mode=ExecutionMode.PAPER_TRADING), StrategyConfig(), RiskLimits(), sim, clock)
    with pytest.raises(RuntimeError, match="refusing to mix"):
        w2.bootstrap()


def test_gateway_is_point_in_time():
    st = make_store(1)
    tr = next(iter(st.tokens.values()))
    now = {"t": dt.datetime.fromtimestamp(tr.obs[10].t + 30, dt.UTC)}
    gw = HistoricalGateway(st, lambda: now["t"])
    got = gw.refresh([tr.pool])
    assert len(got) == 1 and got[0].observed_at.timestamp() == tr.obs[10].t  # last observation <= now, true time
    assert gw.quote("So11111111111111111111111111111111111111112", tr.mint, 10_000_000) is not None
    assert gw.mints([tr.mint])[tr.mint] is None  # no historical mint account: unknown
    fx = gw.eur_usd()
    assert fx.rate_time <= now["t"] and fx.source == "ecb_historical"
