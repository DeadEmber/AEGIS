"""AEGIS command line.

    aegis db upgrade                 apply migrations to AEGIS_DATABASE_URL
    aegis demo [--hours 72]          run the deterministic SIMULATED market (new DB only)
    aegis probe                      check every external data source once (no DB writes)
    aegis once | run [--interval s]  live market data, simulated execution (paper trading)
    aegis status | reconcile         print portfolio state / reconciliation report
    aegis export --out DIR           CSV export of the main tables
    aegis api [--port 8000]          serve the dashboard API
    aegis kill-switch on|off --reason TEXT
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import signal
import sys
import threading
import time
from pathlib import Path

from sqlalchemy import select

from aegis.accounting.portfolio import PortfolioService
from aegis.config import RiskLimits, Settings, StrategyConfig
from aegis.core import logging as alog
from aegis.core.clock import UTC, ManualClock, SystemClock
from aegis.core.hashing import canonical_json
from aegis.risk.controls import KILL_SWITCH
from aegis.storage import repo
from aegis.storage import schema as s
from aegis.storage.db import make_engine, migrate


def _load(args: argparse.Namespace) -> tuple[Settings, StrategyConfig, RiskLimits]:
    settings = Settings()
    if getattr(args, "db", None):
        settings = settings.model_copy(update={"database_url": args.db})
    strategy = StrategyConfig.load(settings.strategy_file)
    return settings, strategy, RiskLimits()


def _print(obj: object) -> None:
    print(json.dumps(json.loads(canonical_json(obj)), indent=2, ensure_ascii=False))


def cmd_db(args: argparse.Namespace) -> int:
    settings, _, _ = _load(args)
    migrate(make_engine(settings.database_url))
    print(f"migrated {settings.database_url}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from aegis.research.simmarket import TOKENS, frozen_mint, make_script
    from aegis.worker.gateway import ScriptedGateway
    from aegis.worker.runner import Worker

    settings, strategy, limits = _load(args)
    url = args.db or "sqlite:///var/aegis-demo.sqlite"
    if url.startswith("sqlite:///") and Path(url.removeprefix("sqlite:///")).exists():
        print("refusing to reuse an existing demo database; choose a new --db", file=sys.stderr)
        return 2
    settings = settings.model_copy(update={"database_url": url})
    start = dt.datetime(2026, 3, 1, tzinfo=UTC)
    clock = ManualClock(start)
    engine = make_engine(url)
    migrate(engine)
    gw = ScriptedGateway(script=make_script(start), clock=clock.now, unsellable={TOKENS["honeypot"]},
                         mint_overrides={TOKENS["frozen"]: frozen_mint(start)})
    strategy.discovery.min_txns_24h = 50
    strategy.discovery.min_unique_traders_24h = 20
    w = Worker(engine, settings, strategy, limits, gw, clock)
    w.bootstrap()
    end = start + dt.timedelta(hours=args.hours)
    while clock.now() < end:
        w.run_cycle()
        clock.advance(minutes=30)
    with engine.connect() as c:
        st = PortfolioService(strategy).state(c, clock.now(), {})
    _print({"WARNING": "SIMULATED market — not evidence of profitability", "db": url,
            "net_worth_eur_excl_open_positions": st.net_worth_liquidable, "realized_pnl_eur": st.realized_pnl_total,
            "open_positions": st.open_positions, "reconciliation_ok": st.reconciliation.ok})
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    from aegis.core.assets import USDC_MINT, WSOL_MINT
    from aegis.worker.gateway import LiveGateway

    settings, strategy, _ = _load(args)
    gw = LiveGateway(settings, strategy)
    checks = {
        "geckoterminal.new_pools": lambda: len(gw.gt.new_pools(1)),
        "dexscreener.profiles": lambda: len(gw.ds.latest_profiles()),
        "jupiter.quote SOL->USDC": lambda: gw.quote(WSOL_MINT, USDC_MINT, 10_000_000).out_amount_raw,
        "solana_rpc.rent(165)": lambda: gw.rpc.rent_exempt_lamports(165),
        "ecb.eur_usd": lambda: str(gw.eur_usd().rate),
        "coingecko.sol_eur": lambda: str(gw.sol_eur().rate),
    }
    ok = True
    for name, fn in checks.items():
        t0 = time.monotonic()
        try:
            res = fn()
            print(f"OK   {name:28s} {res!s:>20}  {1000 * (time.monotonic() - t0):7.0f} ms")
        except Exception as exc:
            ok = False
            print(f"FAIL {name:28s} {exc}")
    return 0 if ok else 1


def _live_worker(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    from aegis.worker.gateway import LiveGateway
    from aegis.worker.runner import Worker

    settings, strategy, limits = _load(args)
    engine = make_engine(settings.database_url)
    migrate(engine)
    w = Worker(engine, settings, strategy, limits, LiveGateway(settings, strategy), SystemClock())
    w.bootstrap()
    return w, settings


def cmd_once(args: argparse.Namespace) -> int:
    w, _ = _live_worker(args)
    rep = w.run_cycle()
    _print(rep.as_dict())
    return 0 if rep.reconciliation_ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    w, settings = _live_worker(args)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    w.run_forever(args.interval or settings.cycle_interval_seconds, stop)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings, _, _ = _load(args)
    engine = make_engine(settings.database_url)
    with engine.connect() as c:
        last = c.execute(select(s.portfolio_snapshots).order_by(s.portfolio_snapshots.c.id.desc()).limit(1)).first()
        hb = c.execute(select(s.worker_heartbeats)).all()
        _print({"data_mode": repo.get_flag(c, "data_mode"), "kill_switch": repo.get_flag(c, KILL_SWITCH),
                "snapshot": dict(last._mapping) if last else None, "workers": [dict(h._mapping) for h in hb]})
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    settings, strategy, _ = _load(args)
    with make_engine(settings.database_url).connect() as c:
        rep = PortfolioService(strategy).reconcile(c)
    _print({"ok": rep.ok, "checks": rep.checks})
    return 0 if rep.ok else 1


def cmd_export(args: argparse.Namespace) -> int:
    settings, _, _ = _load(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tables = ("tokens", "pools", "market_snapshots", "decisions", "orders", "executions", "positions", "position_lots",
              "lot_disposals", "ledger_transactions", "ledger_postings", "contributions", "portfolio_snapshots",
              "shadow_positions", "security_assessments", "exchange_rates")
    with make_engine(settings.database_url).connect() as c:
        for name in tables:
            t = getattr(s, name)
            rows = c.execute(select(t)).all()
            with (out / f"{name}.csv").open("w", newline="", encoding="utf-8") as f:
                wr = csv.writer(f)
                wr.writerow(t.columns.keys())
                for r in rows:
                    wr.writerow([json.dumps(v, default=str) if isinstance(v, dict | list) else v for v in r])
    print(f"exported {len(tables)} tables to {out}")
    return 0


def cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    from aegis.api.app import create_app

    settings, strategy, limits = _load(args)
    engine = make_engine(settings.database_url)
    migrate(engine)
    uvicorn.run(create_app(engine, settings, strategy, limits), host=args.host, port=args.port, log_level="info")
    return 0


def cmd_kill(args: argparse.Namespace) -> int:
    settings, _, _ = _load(args)
    with make_engine(settings.database_url).begin() as c:
        repo.set_flag(c, KILL_SWITCH, args.state, dt.datetime.now(UTC), "cli", args.reason)
    print(f"kill switch {args.state}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aegis", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="override AEGIS_DATABASE_URL")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("db")
    d.add_argument("action", choices=["upgrade"])
    d.set_defaults(fn=cmd_db)
    dm = sub.add_parser("demo")
    dm.add_argument("--hours", type=float, default=72)
    dm.set_defaults(fn=cmd_demo)
    sub.add_parser("probe").set_defaults(fn=cmd_probe)
    sub.add_parser("once").set_defaults(fn=cmd_once)
    r = sub.add_parser("run")
    r.add_argument("--interval", type=int, default=None)
    r.set_defaults(fn=cmd_run)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("reconcile").set_defaults(fn=cmd_reconcile)
    e = sub.add_parser("export")
    e.add_argument("--out", default="exports")
    e.set_defaults(fn=cmd_export)
    a = sub.add_parser("api")
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=8000)
    a.set_defaults(fn=cmd_api)
    k = sub.add_parser("kill-switch")
    k.add_argument("state", choices=["on", "off"])
    k.add_argument("--reason", required=True)
    k.set_defaults(fn=cmd_kill)
    args = p.parse_args(argv)
    alog.configure(Settings().log_level)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
