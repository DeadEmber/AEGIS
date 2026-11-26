"""Point-in-time reference FX for historical runs.

EUR/USD: ECB euro reference rates (stored in `reference/ecb_eurusd_2026.json`, provenance inside). A
rate dated D is only treated as known from D 15:00 UTC; before that (and on weekends/holidays) the
previous publication applies — exactly what a live observer would have had.

SOL/USD comes from the dataset itself (price_usd / price_native of the same observation, or the
source's own SOL quote), so no external SOL history is needed.
"""

from __future__ import annotations

import bisect
import datetime as dt
import json
from decimal import Decimal
from importlib import resources

PUBLICATION_HOUR_UTC = 15


class EcbTable:
    def __init__(self, rates: dict[str, str], source: str):
        items = sorted((dt.date.fromisoformat(k), Decimal(v)) for k, v in rates.items())
        self._known = [dt.datetime(d.year, d.month, d.day, PUBLICATION_HOUR_UTC, tzinfo=dt.UTC).timestamp() for d, _ in items]
        self._rates = [r for _, r in items]
        self._dates = [d for d, _ in items]
        self.source = source

    @classmethod
    def bundled(cls) -> EcbTable:
        doc = json.loads(resources.files("aegis.history").joinpath("reference/ecb_eurusd_2026.json").read_text())
        return cls(doc["rates"], doc["source"])

    def eur_usd(self, t: float) -> tuple[Decimal, dt.date] | None:
        """(USD per EUR, rate date) known at epoch `t`, or None if no rate was published yet."""
        i = bisect.bisect_right(self._known, t) - 1
        if i < 0:
            return None
        return self._rates[i], self._dates[i]

    def coverage(self) -> tuple[dt.date, dt.date]:
        return self._dates[0], self._dates[-1]
