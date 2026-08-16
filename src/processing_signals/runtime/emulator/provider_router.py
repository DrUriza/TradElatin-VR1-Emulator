from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import coinglass, cryptoquant, glassnode
from .fixture_store import FixtureStore, default_input_raw_root
from .overlay import EmulatorOverlay, merge_overlay_response

SUPPORTED_FAMILIES = (
    "prices_ohlcv",
    "cvd_volume_orderflow",
    "open_interest_and_funding",
    "etf_exchange_flows",
    "on_chain_miners",
    "volatility_market_regimes",
    "long_short_liquidations",
    "liquidity_microstructure",
)


@dataclass(frozen=True)
class FamilyFixtureFetcher:
    store: FixtureStore
    family: str
    overlay: EmulatorOverlay | None = None

    def __call__(self, **request: Any) -> Any:
        provider = str(request.get("provider", ""))
        endpoint_id = str(request.get("endpoint_id", ""))
        path = str(request.get("path", ""))
        params = request.get("params") or {}
        if not isinstance(params, Mapping):
            raise TypeError("params must be a mapping")
        if provider == "coinglass":
            response = coinglass.fetch(self.store, self.family, endpoint_id=endpoint_id, path=path, params=params, request=request)
        elif provider == "cryptoquant":
            response = cryptoquant.fetch(self.store, self.family, endpoint_id=endpoint_id, path=path, params=params, request=request)
        elif provider == "glassnode":
            response = glassnode.fetch(self.store, self.family, endpoint_id=endpoint_id, path=path, params=params, request=request)
        else:
            raise ValueError(f"unsupported provider: {provider}")
        if self.overlay is None:
            return response
        overlay_rows = self.overlay.records_for(family=self.family, provider=provider, endpoint_id=endpoint_id, params=params)
        return merge_overlay_response(response, overlay_rows)


class SyntheticProviderRouter:
    """One fixture-backed provider emulator shared by all eight Input families."""

    def __init__(self, input_raw_root: str | Path | None = None, *, overlay_root: str | Path | None = None) -> None:
        self.store = FixtureStore(input_raw_root or default_input_raw_root())
        self.overlay = EmulatorOverlay(overlay_root) if overlay_root is not None else None

    def for_family(self, family: str) -> FamilyFixtureFetcher:
        if family not in SUPPORTED_FAMILIES:
            raise ValueError(f"unsupported family: {family}")
        return FamilyFixtureFetcher(self.store, family, self.overlay)
