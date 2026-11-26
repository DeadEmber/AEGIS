"""Audit P1 — multi-horizon shadow portfolio (finding reproduced on 45240ba: a shadow was closed at day
15 with final multiple x2 taken from a day-1 price)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select, update

from aegis.config import ShadowConfig
from aegis.learning import shadow
from aegis.storage import schema as s
from tests.conftest import T0, new_token

D = Decimal
H = dt.timedelta(hours=1)


def _open(c, t=T0, cohort="rejected", cfg=None, token=None):
    token = token or new_token(c, t=t)
    sid = shadow.open_shadow(c, token_id=token, cohort=cohort, reason="test", t=t, price_eur=D(1), notional_eur=D(1),
                             est_cost_eur=D("0.1"), liquidity_usd=D(20000), cfg=cfg)
    return token, sid


def _row(c, sid):
    return c.execute(select(s.shadow_positions).where(s.shadow_positions.c.id == sid)).first()


def _valuer(qty, price, liq_eur):
    return None if liq_eur is None else (qty * price * D("0.9")).quantize(D("1e-12"))


def test_stale_price_never_used_as_outcome(engine):  # regression of the reproduced finding
    with engine.begin() as c:
        tok, sid = _open(c)
        shadow.update_shadows(c, T0 + H, {tok: (D(2), D(20000), D(18000), T0 + H)}, _valuer)
        shadow.update_shadows(c, T0 + dt.timedelta(days=15), {}, _valuer)  # token no longer observed
        r = _row(c, sid)
    hz = r.outcome["horizons"]
    assert hz["24h"]["status"] == "unknown" and hz["7d"]["status"] == "unknown"
    assert all("gross_multiple" not in v for v in hz.values())
    assert hz["30d"]["status"] == "pending" and r.closed_at is None  # long horizons still censored


def test_fresh_observation_in_window_is_recorded_gross_and_liquidable(engine):
    with engine.begin() as c:
        tok, sid = _open(c)
        shadow.update_shadows(c, T0 + 10 * H, {tok: (D(3), D(20000), D(18000), T0 + 10 * H)}, _valuer)  # peak
        shadow.update_shadows(c, T0 + 24 * H + dt.timedelta(minutes=20),
                              {tok: (D("1.5"), D(9000), D(8000), T0 + 24 * H + dt.timedelta(minutes=20))}, _valuer)
        hz = _row(c, sid).outcome["horizons"]["24h"]
    assert hz["status"] == "observed"
    assert D(hz["gross_multiple"]) == D("1.5") and D(hz["peak_gross_multiple_to_h"]) == D(3)
    assert D(hz["liquidable_multiple"]) == D("0.9") * D("0.9") * D("1.5")  # qty 0.9 x price x 0.9 haircut
    assert D(hz["max_drawdown_to_h"]) == D("0.5")


def test_observation_outside_tolerance_window_is_not_used(engine):
    cfg = ShadowConfig()
    with engine.begin() as c:
        tok, sid = _open(c, cfg=cfg)
        late = T0 + 26 * H  # 24h window tolerance = max(1h, 2% of 24h) = 1h
        shadow.update_shadows(c, late, {tok: (D(5), D(20000), D(18000), late)}, _valuer, cfg)
        hz = _row(c, sid).outcome["horizons"]["24h"]
    assert hz["status"] == "unknown"
    assert shadow.tolerance(24, cfg) == H and shadow.tolerance(365 * 24, cfg) == dt.timedelta(hours=365 * 24 * 0.02)


def test_shadow_closes_only_when_every_horizon_resolved(engine):
    cfg = ShadowConfig(horizons_hours=[24, 168])
    with engine.begin() as c:
        tok, sid = _open(c, cfg=cfg)
        shadow.update_shadows(c, T0 + 24 * H, {tok: (D(2), D(1), D(1), T0 + 24 * H)}, _valuer, cfg)
        assert _row(c, sid).closed_at is None
        t = T0 + 168 * H + dt.timedelta(minutes=5)
        shadow.update_shadows(c, t, {tok: (D(4), D(1), D(1), t)}, _valuer, cfg)
        r = _row(c, sid)
    assert r.closed_at == t and {v["status"] for v in r.outcome["horizons"].values()} == {"observed"}


def test_daily_caps_per_cohort(engine):
    cfg = ShadowConfig(daily_cap={"rejected": 2, "after_exit": None})
    with engine.begin() as c:
        assert all(_open(c, cfg=cfg)[1] for _ in range(2))
        assert _open(c, cfg=cfg)[1] is None  # cap reached today
        assert _open(c, t=T0 + dt.timedelta(days=1), cfg=cfg)[1] is not None
        assert all(_open(c, cohort="after_exit", cfg=cfg)[1] for _ in range(5))  # premature sales: never capped


def test_summary_distinguishes_observed_unknown_censored(engine):
    with engine.begin() as c:
        a, sa = _open(c)
        b, sb = _open(c)
        shadow.update_shadows(c, T0 + 24 * H, {a: (D(2), D(1), D(1), T0 + 24 * H)}, _valuer)
        shadow.update_shadows(c, T0 + 30 * H, {}, _valuer)
        summ = shadow.summary(c)["rejected"]
    assert summ["24h"]["observed"] == 1 and summ["24h"]["unknown"] == 1 and summ["24h"]["censored"] == 0
    assert summ["7d"]["censored"] == 2 and summ["24h"]["gross_ge_x2"] == 1


def test_checkpoint_tokens_when_window_open(engine):
    with engine.begin() as c:
        tok, _ = _open(c)
        assert shadow.checkpoint_tokens(c, T0 + 23 * H) == []
        assert shadow.checkpoint_tokens(c, T0 + 24 * H + dt.timedelta(minutes=10)) == [tok]


def test_legacy_rows_are_not_evaluated_with_stale_prices(engine):
    with engine.begin() as c:
        tok, sid = _open(c)
        c.execute(update(s.shadow_positions).where(s.shadow_positions.c.id == sid).values(outcome={}))  # pre-fix row
        shadow.update_shadows(c, T0 + dt.timedelta(days=20), {}, _valuer)
        r = _row(c, sid)
    assert r.outcome["legacy"] is True and r.closed_at is None
    assert all("gross_multiple" not in v for v in r.outcome["horizons"].values())


def test_units_sold_shadow_measures_forgone_upside(engine):
    from aegis.research.monetisation import forgone_upside

    with engine.begin() as c:
        tok = new_token(c)
        sid = shadow.open_shadow(c, token_id=tok, cohort="after_partial_sell", reason="PRS trim", t=T0,
                                 price_eur=D("0.5"), notional_eur=D(5), est_cost_eur=D(0), liquidity_usd=D(10000),
                                 qty=D(10), sale={"kind": "units_sold", "qty_sold": D(10), "net_proceeds_eur": D(5)})
        sid2 = shadow.open_shadow(c, token_id=tok, cohort="after_partial_sell", reason="second trim", t=T0,
                                  price_eur=D("0.5"), notional_eur=D(5), est_cost_eur=D(0), liquidity_usd=D(10000),
                                  qty=D(10), sale={"kind": "units_sold"})
        assert sid and sid2 and sid != sid2  # several sales of one token are all tracked
        shadow.update_shadows(c, T0 + 24 * H, {tok: (D(2), D(10000), D(9000), T0 + 24 * H)},
                              lambda qty, price, _liq: qty * price, ShadowConfig())
        fu = forgone_upside(c, "24h")
    assert fu["observed"] == 2
    assert fu["forgone_liquidable_eur"] == 2 * (D(10) * D(2) - D(5))  # units now worth 20 vs 5 realised
