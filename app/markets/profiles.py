from types import MappingProxyType

from app.markets.equities_catalog import observable_ids_for
from app.markets.models import (
    Capability,
    CapabilityStatus,
    Family,
    MarketClass,
    MarketProfile,
)


LEGACY_CRYPTO_PROFILE = MarketProfile(
    market_class=MarketClass.CRYPTO,
    profile_id="existing_btc",
    capabilities={
        **{family: Capability(CapabilityStatus.COMPLETE) for family in tuple(Family)[:8]},
        Family.C9: Capability(
            CapabilityStatus.UNSUPPORTED,
            limitation="C9 remains proposed and is not implemented in the Emulator.",
        ),
    },
    implementation_status="OPERATIONAL_FROZEN_33",
)


EQUITIES_PROFILE = MarketProfile(
    market_class=MarketClass.EQUITIES,
    profile_id="us_equity_ssl_ibkr",
    capabilities={
        Family.C1: Capability(CapabilityStatus.COMPLETE, observable_ids_for(Family.C1)),
        Family.C2: Capability(
            CapabilityStatus.PARTIAL,
            observable_ids_for(Family.C2),
            "Aggressor side is estimated and must preserve UNKNOWN volume.",
        ),
        Family.C3: Capability(
            CapabilityStatus.PARTIAL,
            observable_ids_for(Family.C3),
            "Shortable shares only; no options OI, put/call, Greeks or borrow fee.",
        ),
        Family.C4: Capability(
            CapabilityStatus.UNSUPPORTED,
            limitation="No validated institutional or fund-flow source model.",
        ),
        Family.C5: Capability(
            CapabilityStatus.NOT_APPLICABLE,
            limitation="Equities market data is not on-chain evidence.",
        ),
        Family.C6: Capability(
            CapabilityStatus.PARTIAL,
            observable_ids_for(Family.C6),
            "Realized volatility and reference prices only; no structural regime.",
        ),
        Family.C7: Capability(
            CapabilityStatus.UNSUPPORTED,
            limitation="No forced-liquidation source model.",
        ),
        Family.C8: Capability(
            CapabilityStatus.COMPLETE,
            observable_ids_for(Family.C8),
            "Complete only for the frozen visible depth and tape scope.",
        ),
        Family.C9: Capability(
            CapabilityStatus.NOT_APPLICABLE,
            limitation="C9 is the independent proposed blockchain-network workstream.",
        ),
    },
    implementation_status="PROPOSED_NOT_IMPLEMENTED",
)


MARKET_PROFILES = MappingProxyType(
    {
        MarketClass.CRYPTO: LEGACY_CRYPTO_PROFILE,
        MarketClass.EQUITIES: EQUITIES_PROFILE,
    }
)


def get_market_profile(market_class: MarketClass | str) -> MarketProfile:
    """Resolve a profile without changing the current BTC runtime default."""
    try:
        normalized = MarketClass(market_class)
    except ValueError as exc:
        supported = ", ".join(item.value for item in MarketClass)
        raise ValueError(f"Unsupported market class {market_class!r}; expected one of: {supported}") from exc
    return MARKET_PROFILES[normalized]
