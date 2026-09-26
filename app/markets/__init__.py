"""Market-neutral contracts for future VR1 Emulator profiles.

This package is intentionally independent from provider routes and the frozen
endpoint registry.  Importing it cannot register or expose HTTP endpoints.
"""

from app.markets.models import (
    CapabilityStatus,
    DataQuality,
    ExpectedObservable,
    Family,
    MarketClass,
    MarketContext,
    MarketProfile,
    ObservableClass,
    ObservationQuality,
    RawEvent,
    ScenarioDefinition,
    SourceMode,
    SourceStatus,
    VersionedFixture,
)
from app.markets.profiles import EQUITIES_PROFILE, LEGACY_CRYPTO_PROFILE, get_market_profile

__all__ = [
    "CapabilityStatus",
    "DataQuality",
    "EQUITIES_PROFILE",
    "ExpectedObservable",
    "Family",
    "LEGACY_CRYPTO_PROFILE",
    "MarketClass",
    "MarketContext",
    "MarketProfile",
    "ObservableClass",
    "ObservationQuality",
    "RawEvent",
    "ScenarioDefinition",
    "SourceMode",
    "SourceStatus",
    "VersionedFixture",
    "get_market_profile",
]
