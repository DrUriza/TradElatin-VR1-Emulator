"""Multi-market vocabulary and deterministic fixture contracts.

Enum values intentionally match VR1 Processing. This module has no network,
provider, route, or endpoint-registry side effects.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterator, Mapping


class MarketClass(str, Enum):
    CRYPTO = "CRYPTO"
    EQUITIES = "EQUITIES"


class Family(str, Enum):
    C1 = "C1"; C2 = "C2"; C3 = "C3"; C4 = "C4"; C5 = "C5"
    C6 = "C6"; C7 = "C7"; C8 = "C8"; C9 = "C9"


class CapabilityStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ObservableClass(str, Enum):
    RAW = "RAW"
    DERIVED = "DERIVED"
    INFERRED = "INFERRED"


class SourceMode(str, Enum):
    LIVE = "LIVE"
    SYNTHETIC = "SYNTHETIC"
    REPLAY = "REPLAY"


class SourceStatus(str, Enum):
    LIVE = "LIVE"
    FROZEN = "FROZEN"
    DELAYED = "DELAYED"
    DELAYED_FROZEN = "DELAYED_FROZEN"
    REPLAY = "REPLAY"
    UNAVAILABLE = "UNAVAILABLE"


class ObservationQuality(str, Enum):
    VALID = "VALID"
    PARTIAL = "PARTIAL"
    STALE = "STALE"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


def _required(value: str, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name}_must_not_be_empty")
    return normalized


@dataclass(frozen=True)
class Capability:
    status: CapabilityStatus
    observable_ids: tuple[str, ...] = ()
    limitation: str = ""

    def __post_init__(self) -> None:
        if len(set(self.observable_ids)) != len(self.observable_ids):
            raise ValueError("capability_observable_ids_must_be_unique")
        if self.status in {CapabilityStatus.UNSUPPORTED, CapabilityStatus.NOT_APPLICABLE} and self.observable_ids:
            raise ValueError(f"{self.status.value}_capability_cannot_expose_observable_ids")


@dataclass(frozen=True)
class MarketProfile:
    market_class: MarketClass
    profile_id: str
    capabilities: Mapping[Family, Capability]
    implementation_status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _required(self.profile_id, "profile_id"))
        if set(self.capabilities) != set(Family):
            raise ValueError("capability_map_must_cover_c1_c9")
        object.__setattr__(self, "capabilities", MappingProxyType(dict(self.capabilities)))

    def capability_for(self, family: Family | str) -> Capability:
        return self.capabilities[Family(family)]


@dataclass(frozen=True)
class MarketContext:
    market_class: MarketClass
    asset_id: str
    symbol: str
    asset_class: str
    venue: str
    provider: str

    def __post_init__(self) -> None:
        for name in ("asset_id", "symbol", "asset_class", "venue", "provider"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "provider", self.provider.lower())


@dataclass(frozen=True)
class DataQuality:
    event_timestamp: int
    received_timestamp: int
    coverage: float = 1.0
    stale: bool = False
    gap_count: int = 0
    duplicate_count: int = 0
    out_of_order_count: int = 0
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if min(self.event_timestamp, self.received_timestamp) < 0:
            raise ValueError("timestamps_must_be_non_negative")
        if not 0.0 <= self.coverage <= 1.0:
            raise ValueError("coverage_must_be_between_zero_and_one")
        if min(self.gap_count, self.duplicate_count, self.out_of_order_count) < 0:
            raise ValueError("quality_counters_must_be_non_negative")

    def to_mapping(self) -> dict[str, Any]:
        return {"coverage": self.coverage, "stale": self.stale, "gap_count": self.gap_count,
                "duplicate_count": self.duplicate_count, "out_of_order_count": self.out_of_order_count,
                "notes": list(self.notes)}


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    market_class: MarketClass
    asset_id: str
    seed: int
    start_timestamp: int
    periods: int
    cadence_seconds: int
    controlled_noise: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", _required(self.scenario_id, "scenario_id"))
        object.__setattr__(self, "asset_id", _required(self.asset_id, "asset_id"))
        if self.start_timestamp < 0 or self.periods <= 0 or self.cadence_seconds <= 0:
            raise ValueError("scenario_clock_is_invalid")
        if not 0.0 <= self.controlled_noise <= 1.0:
            raise ValueError("controlled_noise_must_be_between_zero_and_one")

    def timestamps(self) -> tuple[int, ...]:
        return tuple(self.start_timestamp + index * self.cadence_seconds for index in range(self.periods))

    def uniform_noise(self, count: int) -> tuple[float, ...]:
        if count < 0:
            raise ValueError("count_must_be_non_negative")
        rng = random.Random(self.seed)
        return tuple(rng.uniform(-self.controlled_noise, self.controlled_noise) for _ in range(count))


@dataclass(frozen=True)
class RawEvent:
    event_id: str
    context: MarketContext
    timestamp: int
    event_type: str
    values: Mapping[str, Any]
    source_status: SourceStatus
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required(self.event_id, "event_id"))
        object.__setattr__(self, "event_type", _required(self.event_type, "event_type"))
        if self.timestamp < 0:
            raise ValueError("timestamp_must_be_non_negative")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


@dataclass(frozen=True)
class ExpectedObservable:
    contract_version: str
    observable_id: str
    family: Family
    context: MarketContext
    event_timestamp: int
    calculated_timestamp: int
    value: Any
    units: str
    source_mode: SourceMode
    source_status: SourceStatus
    quality: ObservationQuality
    capability: CapabilityStatus
    observable_class: ObservableClass
    source_event_ids: tuple[str, ...]
    method_version: str | None = None
    tolerance: float = 0.0
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("contract_version", "observable_id", "units"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if min(self.event_timestamp, self.calculated_timestamp) < 0 or self.tolerance < 0:
            raise ValueError("observable_time_or_tolerance_is_invalid")
        if len(set(self.source_event_ids)) != len(self.source_event_ids):
            raise ValueError("source_event_ids_must_be_unique")
        if self.observable_class is not ObservableClass.RAW and not self.method_version:
            raise ValueError("method_version_required_for_non_raw_observable")
        if self.observable_class is ObservableClass.INFERRED and not self.limitations:
            raise ValueError("limitations_required_for_inferred_observable")


@dataclass(frozen=True)
class VersionedFixture:
    schema_version: str
    fixture_version: str
    scenario: ScenarioDefinition
    source_mode: SourceMode
    data_quality: DataQuality
    raw_events: tuple[RawEvent, ...] = field(default_factory=tuple)
    expected_observables: tuple[ExpectedObservable, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _required(self.schema_version, "schema_version"))
        object.__setattr__(self, "fixture_version", _required(self.fixture_version, "fixture_version"))
        if self.source_mode is SourceMode.LIVE:
            raise ValueError("emulator_fixture_source_mode_must_be_synthetic_or_replay")
        event_ids = [event.event_id for event in self.raw_events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("raw_event_ids_must_be_unique")
        known = set(event_ids)
        for observable in self.expected_observables:
            unknown = set(observable.source_event_ids) - known
            if unknown:
                raise ValueError(f"observable_references_unknown_events:{observable.observable_id}:{sorted(unknown)}")
            if observable.context.market_class is not self.scenario.market_class:
                raise ValueError("observable_market_does_not_match_scenario")
            if observable.context.asset_id != self.scenario.asset_id:
                raise ValueError("observable_asset_does_not_match_scenario")

    def iter_timeline(self) -> Iterator[RawEvent]:
        return iter(sorted(self.raw_events, key=lambda event: (event.timestamp, event.event_id)))
