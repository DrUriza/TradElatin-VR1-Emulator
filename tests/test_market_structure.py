import pytest

from app.endpoint_registry import ENDPOINTS
from app.markets import (
    CapabilityStatus, DataQuality, EQUITIES_PROFILE, ExpectedObservable, Family,
    MarketClass, MarketContext, ObservableClass, ObservationQuality, RawEvent,
    ScenarioDefinition, SourceMode, SourceStatus, VersionedFixture, get_market_profile,
)
from app.markets.equities_catalog import EQUITIES_OBSERVABLES


def _context() -> MarketContext:
    return MarketContext(MarketClass.EQUITIES, "US:NVDA", "NVDA", "equity", "SMART", "IBKR")


def test_frozen_endpoint_registry_remains_crypto_only_and_exactly_33():
    assert len(ENDPOINTS) == 33
    assert sum(item["provider"] == "coinglass" for item in ENDPOINTS) == 20
    assert sum(item["provider"] == "cryptoquant" for item in ENDPOINTS) == 4
    assert sum(item["provider"] == "glassnode" for item in ENDPOINTS) == 9
    assert not any(item["endpoint_id"].startswith("eq_") for item in ENDPOINTS)


def test_profiles_are_separate_and_crypto_remains_legacy_default():
    crypto = get_market_profile("CRYPTO")
    equities = get_market_profile(MarketClass.EQUITIES)
    assert crypto.profile_id == "existing_btc"
    assert crypto.implementation_status == "OPERATIONAL_FROZEN_33"
    assert equities is EQUITIES_PROFILE
    assert equities.implementation_status == "PROPOSED_NOT_IMPLEMENTED"
    assert equities.capability_for(Family.C8).status is CapabilityStatus.COMPLETE
    assert equities.capability_for(Family.C4).status is CapabilityStatus.UNSUPPORTED
    assert equities.capability_for(Family.C9).status is CapabilityStatus.NOT_APPLICABLE


def test_catalog_matches_processing_and_screen_37_ids():
    assert len(EQUITIES_OBSERVABLES) == 37
    assert len(EQUITIES_PROFILE.capability_for(Family.C1).observable_ids) == 8
    assert len(EQUITIES_PROFILE.capability_for(Family.C2).observable_ids) == 8
    assert len(EQUITIES_PROFILE.capability_for(Family.C3).observable_ids) == 1
    assert len(EQUITIES_PROFILE.capability_for(Family.C6).observable_ids) == 4
    assert len(EQUITIES_PROFILE.capability_for(Family.C8).observable_ids) == 16
    assert EQUITIES_OBSERVABLES["eq_c2_estimated_cvd"][1] is ObservableClass.INFERRED


def test_scenario_seed_clock_and_noise_are_reproducible():
    scenario = ScenarioDefinition("normal-market", MarketClass.EQUITIES, "US:NVDA", 4271, 1_800_000_000, 4, 5, 0.05)
    assert scenario.timestamps() == (1_800_000_000, 1_800_000_005, 1_800_000_010, 1_800_000_015)
    assert scenario.uniform_noise(5) == scenario.uniform_noise(5)


def test_fixture_traceability_and_emulator_mode_boundary():
    scenario = ScenarioDefinition("spread-widening", MarketClass.EQUITIES, "US:NVDA", 9, 1_800_000_000, 1, 1)
    quote = RawEvent("quote-1", _context(), scenario.start_timestamp, "LEVEL1", {"bid": 100.0, "ask": 100.25}, SourceStatus.REPLAY)
    spread = ExpectedObservable(
        "vr1-observable-v2-draft", "eq_c8_spread", Family.C8, _context(),
        scenario.start_timestamp, scenario.start_timestamp, 0.25, "USD",
        SourceMode.REPLAY, SourceStatus.REPLAY, ObservationQuality.VALID,
        CapabilityStatus.COMPLETE, ObservableClass.DERIVED, ("quote-1",),
        method_version="spread-absolute-v1",
    )
    fixture = VersionedFixture(
        "vr1-equities-fixture-v1", "1.0.0", scenario, SourceMode.REPLAY,
        DataQuality(scenario.start_timestamp, scenario.start_timestamp), (quote,), (spread,),
    )
    assert tuple(fixture.iter_timeline()) == (quote,)

    with pytest.raises(ValueError, match="unknown_events"):
        VersionedFixture(
            "vr1-equities-fixture-v1", "1.0.0", scenario, SourceMode.REPLAY,
            DataQuality(scenario.start_timestamp, scenario.start_timestamp), (), (spread,),
        )
    with pytest.raises(ValueError, match="synthetic_or_replay"):
        VersionedFixture(
            "vr1-equities-fixture-v1", "1.0.0", scenario, SourceMode.LIVE,
            DataQuality(scenario.start_timestamp, scenario.start_timestamp),
        )


def test_inferred_observable_requires_explicit_limitations():
    with pytest.raises(ValueError, match="limitations_required"):
        ExpectedObservable(
            "vr1-observable-v2-draft", "eq_c2_estimated_cvd", Family.C2, _context(),
            1, 2, 10.0, "shares", SourceMode.REPLAY, SourceStatus.REPLAY,
            ObservationQuality.PARTIAL, CapabilityStatus.PARTIAL,
            ObservableClass.INFERRED, (), method_version="estimated-cvd-v1",
        )
