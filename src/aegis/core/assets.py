"""Asset identity. An asset is ALWAYS (network, address); tickers are display-only metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass

_BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

SOLANA = "solana"
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD8Vw4Cny2cFJ4f3R3R7G9"
# Quote/infra assets never treated as speculative candidates.
SOLANA_QUOTE_ASSETS = frozenset({WSOL_MINT, USDC_MINT, USDT_MINT})

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


@dataclass(frozen=True, slots=True, order=True)
class AssetId:
    network: str
    address: str

    def __post_init__(self) -> None:
        if not self.network or not self.address:
            raise ValueError("AssetId requires network and address")
        if self.network == SOLANA and not _BASE58.match(self.address):
            raise ValueError(f"invalid Solana address: {self.address!r}")

    @property
    def key(self) -> str:
        return f"{self.network}:{self.address}"

    @classmethod
    def parse(cls, key: str) -> AssetId:
        network, _, address = key.partition(":")
        return cls(network, address)

    def __str__(self) -> str:
        return self.key


def is_valid_solana_address(address: str) -> bool:
    return bool(_BASE58.match(address or ""))
