"""Execution modes and the hard guard against real execution.

There is NO code path in AEGIS that signs or submits transactions. `LIVE_DISABLED` exists so that a
deployment configured for "live" fails closed with an explicit error instead of silently simulating.
Enabling real execution would require: owner authorisation, independent validation, a separate
signing service holding keys outside this repository, and new code — see SECURITY.md.
"""

from __future__ import annotations

from aegis.config import ExecutionMode


class LiveExecutionDisabled(RuntimeError):
    pass


SIMULATED_MODES = frozenset(
    {ExecutionMode.HISTORICAL_BACKTEST, ExecutionMode.PAPER_TRADING, ExecutionMode.SHADOW_TRADING}
)


def assert_simulated(mode: ExecutionMode) -> None:
    if mode not in SIMULATED_MODES:
        raise LiveExecutionDisabled(
            f"Execution mode {mode} cannot execute orders. Real execution is not implemented and is "
            "disabled by default; it requires explicit owner authorisation and independent validation."
        )


def books_to_operational_ledger(mode: ExecutionMode) -> bool:
    """Shadow trading records intended orders but never books them into the operational portfolio."""
    return mode in (ExecutionMode.PAPER_TRADING, ExecutionMode.HISTORICAL_BACKTEST)
