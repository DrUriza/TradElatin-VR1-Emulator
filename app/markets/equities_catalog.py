"""Canonical 37 proposed Equities IDs shared with Processing and Screen.

They are internal catalog identifiers, not registered HTTP endpoints.
"""
from types import MappingProxyType

from app.markets.models import CapabilityStatus, Family, ObservableClass


_RAW = ObservableClass.RAW
_DERIVED = ObservableClass.DERIVED
_INFERRED = ObservableClass.INFERRED
_COMPLETE = CapabilityStatus.COMPLETE
_PARTIAL = CapabilityStatus.PARTIAL

_DEFINITIONS = {
    "eq_c1_trade_price": (Family.C1, _RAW, _COMPLETE),
    "eq_c1_best_quote": (Family.C1, _RAW, _COMPLETE),
    "eq_c1_ohlcv_bar": (Family.C1, _RAW, _COMPLETE),
    "eq_c1_previous_close": (Family.C1, _RAW, _COMPLETE),
    "eq_c1_wap": (Family.C1, _RAW, _COMPLETE),
    "eq_c1_reference_quote": (Family.C1, _RAW, _PARTIAL),
    "eq_c1_midpoint": (Family.C1, _DERIVED, _COMPLETE),
    "eq_c1_return": (Family.C1, _DERIVED, _COMPLETE),
    "eq_c2_trade_size": (Family.C2, _RAW, _PARTIAL),
    "eq_c2_trade_count": (Family.C2, _RAW, _PARTIAL),
    "eq_c2_volume_rate": (Family.C2, _DERIVED, _PARTIAL),
    "eq_c2_trade_rate": (Family.C2, _DERIVED, _PARTIAL),
    "eq_c2_estimated_aggressor_volume": (Family.C2, _INFERRED, _PARTIAL),
    "eq_c2_estimated_cvd": (Family.C2, _INFERRED, _PARTIAL),
    "eq_c2_classification_coverage": (Family.C2, _DERIVED, _PARTIAL),
    "eq_c2_large_trade_activity": (Family.C2, _DERIVED, _PARTIAL),
    "eq_c3_shortable_shares": (Family.C3, _RAW, _PARTIAL),
    "eq_c6_realized_volatility": (Family.C6, _DERIVED, _PARTIAL),
    "eq_c6_intraday_range": (Family.C6, _DERIVED, _PARTIAL),
    "eq_c6_gap": (Family.C6, _DERIVED, _PARTIAL),
    "eq_c6_vix_reference": (Family.C6, _RAW, _PARTIAL),
    "eq_c8_top_of_book_size": (Family.C8, _RAW, _COMPLETE),
    "eq_c8_depth_level": (Family.C8, _RAW, _PARTIAL),
    "eq_c8_spread": (Family.C8, _DERIVED, _COMPLETE),
    "eq_c8_top_imbalance": (Family.C8, _DERIVED, _COMPLETE),
    "eq_c8_microprice": (Family.C8, _DERIVED, _COMPLETE),
    "eq_c8_visible_bid_depth": (Family.C8, _DERIVED, _PARTIAL),
    "eq_c8_visible_ask_depth": (Family.C8, _DERIVED, _PARTIAL),
    "eq_c8_depth_imbalance": (Family.C8, _DERIVED, _PARTIAL),
    "eq_c8_depth_concentration": (Family.C8, _DERIVED, _PARTIAL),
    "eq_c8_displayed_liquidity_change": (Family.C8, _DERIVED, _PARTIAL),
    "eq_c8_large_visible_order": (Family.C8, _DERIVED, _PARTIAL),
    "eq_c8_visible_liquidity_persistence": (Family.C8, _DERIVED, _PARTIAL),
    "eq_c8_time_sales": (Family.C8, _RAW, _PARTIAL),
    "eq_c8_large_liquidity_activity": (Family.C8, _INFERRED, _PARTIAL),
    "eq_c8_observable_sweep_behavior": (Family.C8, _INFERRED, _PARTIAL),
    "eq_c8_depth_tape_alignment": (Family.C8, _INFERRED, _PARTIAL),
}

EQUITIES_OBSERVABLES = MappingProxyType(_DEFINITIONS)


def observable_ids_for(family: Family) -> tuple[str, ...]:
    return tuple(observable_id for observable_id, definition in EQUITIES_OBSERVABLES.items() if definition[0] is family)
