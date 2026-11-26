"""Security & Fraud Detection Engine.

Security prevails over opportunity. Each check yields a Finding with an evidence level:
- CONFIRMED: verified on-chain fact or executable-quote fact (e.g. freeze authority present).
- INDICATOR: statistical/behavioural sign of risk (e.g. low organic volume score).
- UNKNOWN: the check could not be evaluated. Unknown is NEVER interpreted as safe; for critical
  checks it blocks purchases when `unknown_critical_blocks` is set (default).
This engine is protective only: it excludes risky markets; it never manipulates them.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from aegis.config import SecurityConfig
from aegis.core.hashing import stable_hash
from aegis.execution.types import SwapQuote
from aegis.marketdata.jupiter import quote_roundtrip_loss_pct
from aegis.marketdata.models import MintInfo, PoolObservation, TokenProfile

ENGINE_VERSION = "security-0.2"
_MISSING = object()


class Status(enum.StrEnum):
    PASS = "PASS"
    RISK = "RISK"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class Evidence(enum.StrEnum):
    CONFIRMED = "CONFIRMED"
    INDICATOR = "INDICATOR"
    UNKNOWN = "UNKNOWN"


class Verdict(enum.StrEnum):
    PASS = "PASS"  # no critical issue, no major risk
    RISK = "RISK"  # tradable but with risk indicators (affects sizing/scores)
    BLOCK = "BLOCK"  # purchases forbidden
    UNKNOWN = "UNKNOWN"  # not assessed yet


@dataclass(frozen=True)
class Finding:
    check: str
    status: Status
    critical: bool
    evidence: Evidence
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SecurityInputs:
    token_address: str
    mint: MintInfo | None = None
    mint_fetch_failed: bool = False
    gt_profile: TokenProfile | None = None
    jup_profile: TokenProfile | None = None
    pool: PoolObservation | None = None
    liquidity_eur: Decimal | None = None
    buy_quote: SwapQuote | None = None
    sell_quote: SwapQuote | None = None
    quotes_attempted: bool = False
    quote_fetch_failed: bool = False


@dataclass(frozen=True)
class Assessment:
    verdict: Verdict
    findings: tuple[Finding, ...]
    inputs_hash: str
    engine_version: str = ENGINE_VERSION

    @property
    def blocking_reasons(self) -> list[str]:
        return [f"{f.check}:{f.status}" for f in self.findings
                if f.critical and f.status in (Status.CRITICAL, Status.UNKNOWN)]

    @property
    def risk_count(self) -> int:
        return sum(1 for f in self.findings if f.status == Status.RISK)

    def to_json(self) -> list[dict[str, Any]]:
        return [{**asdict(f), "status": str(f.status), "evidence": str(f.evidence)} for f in self.findings]


DANGEROUS_EXTENSIONS = {
    "permanentDelegate": "issuer can move/burn any holder's tokens",
    "nonTransferable": "tokens cannot be transferred (unsellable)",
    "pausable": "issuer can pause all transfers",
}


class SecurityEngine:
    def __init__(self, cfg: SecurityConfig):
        self.cfg = cfg

    def assess(self, x: SecurityInputs) -> Assessment:
        f: list[Finding] = []
        f += self._authorities(x)
        f += self._extensions(x)
        f += self._sellability(x)
        f += self._honeypot_flag(x)
        f += self._concentration(x)
        f += self._liquidity(x)
        f += self._manipulation_indicators(x)
        verdict = self._verdict(f)
        h = stable_hash({"token": x.token_address, "findings": [(i.check, str(i.status)) for i in f]})
        return Assessment(verdict, tuple(f), h)

    # ------------------------------------------------------------------ checks
    def _authority(self, name: str, onchain: object, flags: list[bool | None], block: bool) -> Finding:
        """`onchain` is the authority from the mint account, None if revoked, or _MISSING if unavailable."""
        if onchain is not _MISSING:
            if onchain is None:
                return Finding(name, Status.PASS, block, Evidence.CONFIRMED, f"{name} revoked (on-chain)")
            return Finding(name, Status.CRITICAL if block else Status.RISK, block, Evidence.CONFIRMED,
                           f"{name} present (on-chain)", {"authority": onchain})
        known = [v for v in flags if v is not None]
        if not known:
            return Finding(name, Status.UNKNOWN, block, Evidence.UNKNOWN, f"{name} not verifiable")
        if all(known):
            return Finding(name, Status.PASS, block, Evidence.INDICATOR, f"{name} reported disabled by data providers")
        return Finding(name, Status.CRITICAL if block else Status.RISK, block, Evidence.INDICATOR,
                       f"{name} reported ACTIVE by a data provider")

    def _authorities(self, x: SecurityInputs) -> list[Finding]:
        profiles = [p for p in (x.gt_profile, x.jup_profile) if p]
        mint_auth: object = x.mint.mint_authority if x.mint else _MISSING
        freeze_auth: object = x.mint.freeze_authority if x.mint else _MISSING
        return [
            self._authority("mint_authority", mint_auth, [p.mint_authority_disabled for p in profiles],
                            self.cfg.block_mint_authority),
            self._authority("freeze_authority", freeze_auth, [p.freeze_authority_disabled for p in profiles],
                            self.cfg.block_freeze_authority),
        ]

    def _extensions(self, x: SecurityInputs) -> list[Finding]:
        if x.mint is None:
            return [Finding("token_extensions", Status.UNKNOWN, True, Evidence.UNKNOWN, "mint account not available")]
        if x.mint.program != "spl-token-2022":
            return [Finding("token_extensions", Status.PASS, True, Evidence.CONFIRMED, "classic SPL token")]
        out: list[Finding] = []
        ex = x.mint.extensions
        for name, why in DANGEROUS_EXTENSIONS.items():
            if name in ex:
                block = (name == "permanentDelegate" and self.cfg.block_permanent_delegate) or \
                        (name == "nonTransferable" and self.cfg.block_non_transferable) or name == "pausable"
                out.append(Finding(f"ext:{name}", Status.CRITICAL if block else Status.RISK, block, Evidence.CONFIRMED,
                                   why, {"state": ex[name]}))
        if "transferHook" in ex:
            program = (ex["transferHook"] or {}).get("programId")
            auth = (ex["transferHook"] or {}).get("authority")
            if program:
                out.append(Finding("ext:transferHook", Status.CRITICAL if self.cfg.block_transfer_hook else Status.RISK,
                                   self.cfg.block_transfer_hook, Evidence.CONFIRMED,
                                   "active transfer hook can reject or tax transfers", {"program": program}))
            elif auth:
                out.append(Finding("ext:transferHook", Status.RISK, False, Evidence.CONFIRMED,
                                   "transfer hook inactive but authority can enable it", {"authority": auth}))
        if "transferFeeConfig" in ex:
            st = ex["transferFeeConfig"] or {}
            bps = max(int((st.get(k) or {}).get("transferFeeBasisPoints") or 0)
                      for k in ("newerTransferFee", "olderTransferFee"))
            if bps > self.cfg.max_transfer_fee_bps:
                out.append(Finding("ext:transferFee", Status.CRITICAL, True, Evidence.CONFIRMED,
                                   f"transfer fee {bps} bps", {"bps": bps}))
            elif st.get("transferFeeConfigAuthority"):
                out.append(Finding("ext:transferFee", Status.RISK, False, Evidence.CONFIRMED,
                                   "transfer fee currently 0 but authority can raise it"))
        if "defaultAccountState" in ex and str((ex["defaultAccountState"] or {}).get("accountState")).lower() == "frozen":
            out.append(Finding("ext:defaultAccountState", Status.CRITICAL, self.cfg.block_default_account_frozen,
                               Evidence.CONFIRMED, "new token accounts are frozen by default"))
        if "mintCloseAuthority" in ex:
            out.append(Finding("ext:mintCloseAuthority", Status.RISK, False, Evidence.CONFIRMED, "mint can be closed"))
        if "confidentialTransferMint" in ex:
            out.append(Finding("ext:confidentialTransfer", Status.RISK, False, Evidence.CONFIRMED,
                               "confidential transfers hide balances/flows"))
        if not out:
            out.append(Finding("token_extensions", Status.PASS, True, Evidence.CONFIRMED,
                               "Token-2022 without dangerous extensions", {"extensions": sorted(ex)}))
        return out

    def _sellability(self, x: SecurityInputs) -> list[Finding]:
        crit = self.cfg.require_sell_route
        if not x.quotes_attempted:
            return [Finding("sell_route", Status.UNKNOWN, crit, Evidence.UNKNOWN, "sell route not checked")]
        if x.quote_fetch_failed:
            return [Finding("sell_route", Status.UNKNOWN, crit, Evidence.UNKNOWN, "quote provider error (transient)")]
        if x.sell_quote is None:
            return [Finding("sell_route", Status.CRITICAL, crit, Evidence.CONFIRMED,
                            "no executable sell route found by aggregator")]
        out = [Finding("sell_route", Status.PASS, crit, Evidence.CONFIRMED, "executable sell route exists",
                       {"route": list(x.sell_quote.route_labels)})]
        if x.buy_quote is not None:
            loss = quote_roundtrip_loss_pct(x.buy_quote, x.sell_quote)
            if loss is None:
                out.append(Finding("roundtrip_loss", Status.UNKNOWN, True, Evidence.UNKNOWN, "cannot compute"))
            elif loss > self.cfg.max_roundtrip_loss_pct:
                out.append(Finding("roundtrip_loss", Status.CRITICAL, True, Evidence.CONFIRMED,
                                   f"round-trip loss {loss:.2f}% (honeypot-like / hidden tax)", {"loss_pct": str(loss)}))
            else:
                out.append(Finding("roundtrip_loss", Status.PASS, True, Evidence.CONFIRMED,
                                   f"round-trip loss {loss:.2f}%", {"loss_pct": str(loss)}))
        return out

    def _honeypot_flag(self, x: SecurityInputs) -> list[Finding]:
        v = (x.gt_profile.honeypot if x.gt_profile else None) or None
        if v == "yes":
            return [Finding("provider_honeypot_flag", Status.CRITICAL, True, Evidence.INDICATOR,
                            "data provider flags token as honeypot")]
        if v == "no":
            return [Finding("provider_honeypot_flag", Status.PASS, False, Evidence.INDICATOR, "provider: not honeypot")]
        return [Finding("provider_honeypot_flag", Status.UNKNOWN, False, Evidence.UNKNOWN, f"provider flag: {v}")]

    def _concentration(self, x: SecurityInputs) -> list[Finding]:
        out = []
        tops = [p.top10_pct for p in (x.jup_profile, x.gt_profile) if p and p.top10_pct is not None]
        if not tops:
            out.append(Finding("holder_concentration", Status.UNKNOWN, False, Evidence.UNKNOWN, "no holder data"))
        else:
            top = max(tops)  # conservative across providers (may include pool vaults: documented caveat)
            st = Status.CRITICAL if top > self.cfg.max_top10_holder_pct else (Status.RISK if top > 30 else Status.PASS)
            out.append(Finding("holder_concentration", st, True, Evidence.INDICATOR, f"top10 holders {top}%",
                               {"top10_pct": str(top)}))
        devs = [p.dev_holding_pct for p in (x.gt_profile, x.jup_profile) if p and p.dev_holding_pct is not None]
        if devs:
            dev = max(devs)
            st = Status.CRITICAL if dev > self.cfg.max_dev_holding_pct else (Status.RISK if dev > 5 else Status.PASS)
            out.append(Finding("creator_exposure", st, True, Evidence.INDICATOR, f"developer holds {dev}%"))
        if x.jup_profile and isinstance(x.jup_profile.extra.get("dev_mints"), int) and x.jup_profile.extra["dev_mints"] > 20:
            out.append(Finding("creator_history", Status.RISK, False, Evidence.INDICATOR,
                               f"creator launched {x.jup_profile.extra['dev_mints']} tokens (serial launcher)"))
        return out

    def _liquidity(self, x: SecurityInputs) -> list[Finding]:
        if x.liquidity_eur is None:
            return [Finding("liquidity", Status.UNKNOWN, True, Evidence.UNKNOWN, "liquidity unknown")]
        if x.liquidity_eur < Decimal(2000):
            return [Finding("liquidity", Status.CRITICAL, True, Evidence.CONFIRMED,
                            f"liquidity {x.liquidity_eur:.0f} EUR insufficient")]
        return [Finding("liquidity", Status.PASS, True, Evidence.CONFIRMED, f"liquidity {x.liquidity_eur:.0f} EUR")]

    def _manipulation_indicators(self, x: SecurityInputs) -> list[Finding]:
        out: list[Finding] = []
        jp = x.jup_profile
        if jp and jp.organic_score is not None and jp.organic_score < Decimal(10):
            out.append(Finding("organic_activity", Status.RISK, False, Evidence.INDICATOR,
                               f"low organic score {jp.organic_score:.1f}/100 (possible bot/wash volume)"))
        p = x.pool
        if p is not None:
            w = p.w("h24")
            if w.volume_usd is not None and p.liquidity_usd and p.liquidity_usd > 0:
                turnover = w.volume_usd / p.liquidity_usd
                if turnover > 50:
                    out.append(Finding("volume_liquidity_ratio", Status.RISK, False, Evidence.INDICATOR,
                                       f"24h volume = {turnover:.0f}x liquidity (wash-trading indicator)"))
            if w.buys is not None and w.sells is not None and w.buyers and w.sellers:
                tpt = Decimal(w.buys + w.sells) / Decimal(w.buyers + w.sellers)
                if tpt > 15:
                    out.append(Finding("trades_per_trader", Status.RISK, False, Evidence.INDICATOR,
                                       f"{tpt:.1f} trades per active address (bot concentration)"))
        return out

    def _verdict(self, findings: list[Finding]) -> Verdict:
        if any(f.critical and f.status == Status.CRITICAL for f in findings):
            return Verdict.BLOCK
        if any(f.status == Status.CRITICAL for f in findings):
            return Verdict.BLOCK
        if self.cfg.unknown_critical_blocks and any(f.critical and f.status == Status.UNKNOWN for f in findings):
            return Verdict.BLOCK
        if any(f.status == Status.RISK for f in findings):
            return Verdict.RISK
        return Verdict.PASS

