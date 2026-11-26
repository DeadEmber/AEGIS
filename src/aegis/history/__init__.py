"""Historical empirical base (Phase 2, block A).

Public historical datasets are registered with pinned revisions, hashes and licences
(`registry`), normalised into point-in-time observations that keep retrospective outcomes in a
separate, explicitly-labelled table (`store`), and served either to the unchanged production worker
(`gateway.HistoricalGateway` for `Worker.replay`) or to the fast research simulator
(`aegis.research.backtest`). Nothing is fabricated: missing data stays `None` with a reason.
"""
