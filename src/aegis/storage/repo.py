"""Repository helpers: upserts and point-in-time queries shared by the worker and the API."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.engine import Connection

from aegis.core.clock import ensure_utc
from aegis.features.library import HistoryPoint
from aegis.marketdata.fx import usd_to_eur
from aegis.marketdata.models import WINDOWS, FxRate, PoolObservation, TokenProfile
from aegis.storage import schema as s


def upsert_token(conn: Connection, obs: PoolObservation, source: str) -> int:
    row = conn.execute(select(s.tokens.c.id, s.tokens.c.symbol, s.tokens.c.decimals).where(
        and_(s.tokens.c.network == obs.network, s.tokens.c.address == obs.token_address))).first()
    t = obs.observed_at
    if row is None:
        return int(conn.execute(insert(s.tokens).values(
            network=obs.network, address=obs.token_address, symbol=obs.token_symbol, name=obs.token_name,
            decimals=obs.token_decimals, first_seen_at=t, last_seen_at=t, discovery_source=source, universes=[],
            market_state="DISCOVERED", market_state_since=t, explosion_count=0, entries=0, meta={},
        )).inserted_primary_key[0])
    vals: dict[str, Any] = {"last_seen_at": t}
    if obs.token_symbol and not row.symbol:
        vals["symbol"], vals["name"] = obs.token_symbol, obs.token_name
    if obs.token_decimals is not None and row.decimals is None:
        vals["decimals"] = obs.token_decimals
    conn.execute(update(s.tokens).where(s.tokens.c.id == row.id).values(**vals))
    return int(row.id)


def upsert_pool(conn: Connection, token_id: int, obs: PoolObservation) -> int:
    row = conn.execute(select(s.pools.c.id).where(
        and_(s.pools.c.network == obs.network, s.pools.c.address == obs.pool_address))).first()
    if row is None:
        return int(conn.execute(insert(s.pools).values(
            network=obs.network, address=obs.pool_address, token_id=token_id, token_side=obs.token_side,
            counter_asset=obs.counter_asset, dex=obs.dex, pool_type=obs.pool_type, source_created_at=obs.pool_created_at,
            first_seen_at=obs.observed_at, last_seen_at=obs.observed_at, is_primary=False,
        )).inserted_primary_key[0])
    conn.execute(update(s.pools).where(s.pools.c.id == row.id).values(last_seen_at=obs.observed_at))
    return int(row.id)


def set_primary_pool(conn: Connection, token_id: int, pool_id: int) -> None:
    conn.execute(update(s.pools).where(s.pools.c.token_id == token_id).values(is_primary=False))
    conn.execute(update(s.pools).where(s.pools.c.id == pool_id).values(is_primary=True))


def insert_snapshot(conn: Connection, token_id: int, pool_id: int, obs: PoolObservation, eur_usd: Decimal,
                    fx_id: int | None) -> int:
    vals: dict[str, Any] = dict(
        token_id=token_id, pool_id=pool_id, source=obs.source, observed_at=obs.observed_at, event_time=None,
        price_usd=obs.price_usd, price_eur=usd_to_eur(obs.price_usd, eur_usd), eur_usd_rate_id=fx_id,
        liquidity_usd=obs.liquidity_usd, fdv_usd=obs.fdv_usd, market_cap_usd=obs.market_cap_usd,
        quality_flags=list(obs.quality_flags), raw=obs.raw,
    )
    for w in WINDOWS:
        ws = obs.w(w)
        vals[f"volume_{w}_usd"] = ws.volume_usd
        vals[f"price_change_{w}"] = ws.price_change_pct
        vals[f"buys_{w}"], vals[f"sells_{w}"] = ws.buys, ws.sells
        vals[f"buyers_{w}"], vals[f"sellers_{w}"] = ws.buyers, ws.sellers
    return int(conn.execute(insert(s.market_snapshots).values(**vals)).inserted_primary_key[0])


def insert_profile(conn: Connection, token_id: int, p: TokenProfile) -> None:
    conn.execute(insert(s.token_profiles).values(
        token_id=token_id, source=p.source, observed_at=p.observed_at, holders_count=p.holders_count,
        top10_pct=p.top10_pct, dev_holding_pct=p.dev_holding_pct, circulating_supply=p.circulating_supply,
        total_supply=p.total_supply, market_cap_usd=p.market_cap_usd, organic_score=p.organic_score,
        mint_authority=_flag(p.mint_authority_disabled), freeze_authority=_flag(p.freeze_authority_disabled),
        raw={"extra": {k: v for k, v in p.extra.items() if k in ("organic_label", "dev_mints", "tags", "stats24h")}},
    ))


def _flag(disabled: bool | None) -> str | None:
    return None if disabled is None else ("disabled" if disabled else "active")


def history(conn: Connection, token_id: int, as_of: dt.datetime, hours: float = 30) -> list[HistoryPoint]:
    start = ensure_utc(as_of) - dt.timedelta(hours=hours)
    rows = conn.execute(
        select(s.market_snapshots.c.observed_at, s.market_snapshots.c.price_usd, s.market_snapshots.c.liquidity_usd,
               s.pools.c.address)
        .join(s.pools, s.pools.c.id == s.market_snapshots.c.pool_id)
        .where(and_(s.market_snapshots.c.token_id == token_id, s.market_snapshots.c.observed_at >= start,
                    s.market_snapshots.c.observed_at <= as_of))
        .order_by(s.market_snapshots.c.observed_at)
    )
    return [HistoryPoint(r.observed_at, r.price_usd, r.liquidity_usd, r.address) for r in rows]


def latest_snapshot(conn: Connection, token_id: int) -> Any:
    return conn.execute(
        select(s.market_snapshots, s.pools.c.address.label("pool_address"), s.pools.c.pool_type)
        .join(s.pools, s.pools.c.id == s.market_snapshots.c.pool_id)
        .where(s.market_snapshots.c.token_id == token_id)
        .order_by(s.market_snapshots.c.observed_at.desc(), s.market_snapshots.c.id.desc()).limit(1)
    ).first()


def store_fx(conn: Connection, fx: FxRate) -> int:
    row = conn.execute(select(s.exchange_rates.c.id).where(and_(
        s.exchange_rates.c.base == fx.base, s.exchange_rates.c.quote == fx.quote, s.exchange_rates.c.source == fx.source,
        s.exchange_rates.c.rate_time == fx.rate_time, s.exchange_rates.c.rate == fx.rate))).first()
    if row is not None:
        return int(row.id)
    return int(conn.execute(insert(s.exchange_rates).values(
        base=fx.base, quote=fx.quote, rate=fx.rate, source=fx.source, rate_time=fx.rate_time, fetched_at=fx.fetched_at,
    )).inserted_primary_key[0])


def latest_profile(conn: Connection, token_id: int, as_of: dt.datetime) -> Any:
    return conn.execute(select(s.token_profiles).where(and_(
        s.token_profiles.c.token_id == token_id, s.token_profiles.c.observed_at <= as_of))
        .order_by(s.token_profiles.c.observed_at.desc()).limit(1)).first()


def latest_security(conn: Connection, token_id: int) -> Any:
    return conn.execute(select(s.security_assessments).where(s.security_assessments.c.token_id == token_id)
                        .order_by(s.security_assessments.c.assessed_at.desc()).limit(1)).first()


def record_event(conn: Connection, table: Any, t: dt.datetime, **vals: Any) -> None:
    key = "occurred_at"
    conn.execute(insert(table).values(**{key: t}, **vals))


def count(conn: Connection, table: Any, *where: Any) -> int:
    q = select(func.count()).select_from(table)
    for w in where:
        q = q.where(w)
    return int(conn.execute(q).scalar_one())


def get_flag(conn: Connection, key: str) -> str | None:
    row = conn.execute(select(s.control_flags.c.value).where(s.control_flags.c.key == key)).first()
    return None if row is None else row.value


def set_flag(conn: Connection, key: str, value: str, t: dt.datetime, actor: str, reason: str = "") -> None:
    if conn.execute(select(s.control_flags.c.key).where(s.control_flags.c.key == key)).first() is None:
        conn.execute(insert(s.control_flags).values(key=key, value=value, updated_at=t, updated_by=actor, reason=reason))
    else:
        conn.execute(update(s.control_flags).where(s.control_flags.c.key == key).values(
            value=value, updated_at=t, updated_by=actor, reason=reason))
    conn.execute(insert(s.audit_logs).values(occurred_at=t, actor=actor, action=f"set_flag:{key}",
                                             details={"value": value, "reason": reason}))
