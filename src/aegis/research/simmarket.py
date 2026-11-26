"""Deterministic SIMULATED market used by `aegis demo` and integration tests.

Every observation produced here is labelled source='simulated'. It is NOT market evidence and must
never be used to claim profitability. Paths are chosen to exercise specific behaviours:
explosion + collapse, slow death, stable chop, unsellable token, freeze-authority token.
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

from aegis.marketdata.models import MintInfo, PoolObservation, WindowStats

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def addr(tag: str) -> str:
    body = "".join(B58[(ord(ch) * 7 + i) % 58] for i, ch in enumerate(tag * 8))[:36]
    return ("Sim" + body).ljust(40, "1")[:40]


def _path(kind: str, h: float) -> tuple[float, float, float]:
    """(price_usd, liquidity_usd, buyer_share) at hour h since start."""
    if kind == "rocket":  # x10 over 30h, peak, then -85% collapse
        p = 2e-05 * math.exp(math.log(10) * h / 30) if h < 30 else 0.0002 * math.exp(-0.08 * (h - 30))
        liq = 30000 * (1 + 3 * min(h, 30) / 30) * (0.6 if h > 40 else 1)
        bs = 0.68 if h < 26 else 0.38
        return p, liq, bs
    if kind == "fade":  # slow death
        return 0.00004 * math.exp(-0.05 * h), max(300.0, 25000 * math.exp(-0.06 * h)), 0.42
    if kind == "chop":
        return 0.00003 * (1 + 0.05 * math.sin(h / 3)), 40000.0, 0.52
    return 0.00005 * (1 + 0.02 * math.sin(h)), 60000.0, 0.55  # 'honeypot' / 'frozen' look healthy


TOKENS = {"rocket": addr("rocket"), "fade": addr("fade"), "chop": addr("chop"),
          "honeypot": addr("honeypot"), "frozen": addr("frozen")}


def make_script(start: dt.datetime, eur_usd: Decimal = Decimal("1.0935")):
    def script(t: dt.datetime) -> list[PoolObservation]:
        h = (t - start).total_seconds() / 3600
        out = []
        for kind, token in TOKENS.items():
            p, liq, bs = _path(kind, h)
            p1, _, _ = _path(kind, max(0.0, h - 1))
            p6, _, _ = _path(kind, max(0.0, h - 6))
            p24, _, _ = _path(kind, max(0.0, h - 24))
            pm5, _, _ = _path(kind, max(0.0, h - 5 / 60))
            win = {}
            for w, ref, hours in (("m5", pm5, 5 / 60), ("h1", p1, 1), ("h6", p6, 6), ("h24", p24, 24)):
                traders = max(10, int(400 * hours ** 0.5))
                buyers = int(traders * bs)
                win[w] = WindowStats(volume_usd=Decimal(str(round(liq * 0.4 * hours / 24 + 50, 2))),
                                     price_change_pct=Decimal(str(round((p / ref - 1) * 100, 6))),
                                     buys=buyers * 2, sells=(traders - buyers) * 2, buyers=buyers, sellers=traders - buyers)
            out.append(PoolObservation(
                source="simulated", network="solana", pool_address=token[::-1][:40], token_address=token,
                token_side="base", counter_asset="So11111111111111111111111111111111111111112", dex="simdex",
                pool_type="amm", observed_at=t, price_usd=Decimal(str(p)), liquidity_usd=Decimal(str(round(liq, 2))),
                fdv_usd=Decimal(str(round(p * 1e9, 2))), market_cap_usd=Decimal(str(round(p * 1e9, 2))), windows=win,
                pool_created_at=start - dt.timedelta(days=3), token_symbol=kind.upper(), token_name=f"SIM {kind}",
                token_decimals=6, quality_flags=("simulated",),
            ))
        return out

    return script


def frozen_mint(t: dt.datetime) -> MintInfo:
    return MintInfo(TOKENS["frozen"], "spl-token", 6, 10**15, None, "FreezeAuth1111111111111111111111", True, {}, t)
