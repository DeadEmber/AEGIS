"""SYNTHETIC historical stores for tests (shape-compatible with the real adapters; never research evidence).

Real dataset rows are not committed: Launch Tape forbids redistribution and the corpus is large. These
builders produce small, deterministic stores with internally consistent CPMM reserves."""

from __future__ import annotations

import datetime as dt
import math

from aegis.history.markets import MarketType
from aegis.history.store import HistoricalStore, Obs, Outcome, TokenTrack

T0 = dt.datetime(2026, 7, 6, 12, 0, tzinfo=dt.UTC).timestamp()
SOL_USD = 80.0
SUPPLY = 1_000_000_000.0

PATHS = {
    # multiple of the graduation price as a function of minutes since graduation
    "pump": lambda m: 1 + 2.5 * math.sin(min(m, 90) / 90 * math.pi / 2) if m <= 90 else max(0.3, 3.5 - (m - 90) / 20),
    "dump": lambda m: max(0.05, 1 - m / 40),
    "flat": lambda m: 1.0 + 0.01 * math.sin(m),
}


def make_track(mint: str, path: str, grad_t: float, *, minutes: int = 240, step_s: int = 60, price0_sol: float = 4e-7,
               liq_sol0: float = 170.0, buys: int = 60, sells: int = 20, market_type: MarketType = MarketType.CPMM) -> TokenTrack:
    tr = TokenTrack(mint=mint, symbol=mint[:4].upper(), created_at=grad_t - 900, graduated_at=grad_t, pool=f"pool-{mint}",
                    supply=SUPPLY, decimals=6)
    tr.static["seconds_to_graduation"] = {"value": 900.0, "known_at": grad_t, "src": "synthetic"}
    f = PATHS[path]
    t = grad_t + step_s
    while t <= grad_t + minutes * 60:
        m = (t - grad_t) / 60
        mult = f(m)
        p_sol = price0_sol * mult
        res_sol = liq_sol0 / 2 * math.sqrt(mult)  # constant-product: quote reserve grows with sqrt(price)
        res_tok = res_sol / p_sol
        prev = f(max(0.0, m - 5))
        chg5 = (mult / prev - 1) * 100 if prev else None
        tr.obs.append(Obs(t=t, pool=tr.pool or "", market_type=market_type, price_usd=p_sol * SOL_USD, price_sol=p_sol,
                          sol_usd=SOL_USD, liq_usd=2 * res_sol * SOL_USD, res_token=res_tok, res_sol=res_sol,
                          mcap_usd=p_sol * SUPPLY * SOL_USD, fdv_usd=p_sol * SUPPLY * SOL_USD,
                          vol=(5000.0, 40000.0, None, None), chg=(chg5, None, None, None),
                          buys=(buys, buys * 6), sells=(sells, sells * 6), since_grad_s=t - grad_t))
        t += step_s
    tr.tracking_end = tr.obs[-1].t if tr.obs else None
    return tr


def make_store(n_each: int = 3, *, minutes: int = 240) -> HistoricalStore:
    tokens = {}
    k = 0
    for path in ("pump", "dump", "flat"):
        for i in range(n_each):
            mint = f"{path}{i}" + "x" * 30
            tokens[mint] = make_track(mint, path, T0 + k * 300, minutes=minutes)
            k += 1
    outcomes = {m: Outcome(m, "synthetic", {"peak_multiple": 3.5 if m.startswith("pump") else 1.0}, T0 + 86400)
                for m in tokens}
    return HistoricalStore("synthetic", "test", tokens, outcomes, resolution_profile=[(math.inf, 60.0)]).finalize()
