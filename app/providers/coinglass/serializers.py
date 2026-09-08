from __future__ import annotations

import math
from datetime import datetime, timezone

from app.core.market_state import MarketState
from app.core.series import Candle
from app.settings import EMULATOR_RECORD_COUNT, SIMULATION_UPDATE_SECONDS


def _str_number(value: float) -> str:
    return format(float(value), ".12g")


def _envelope(data, *, success: bool | None = None) -> dict:
    payload = {"code": "0", "msg": "success", "data": data}
    if success is not None:
        payload["success"] = success
    return payload


def serialize_ohlc(candles: list[Candle], *, strings: bool = True, include_volume: bool = False) -> dict:
    data = []
    for candle in candles:
        item = {
            "time": candle.timestamp * 1000,
            "open": _str_number(candle.open) if strings else candle.open,
            "high": _str_number(candle.high) if strings else candle.high,
            "low": _str_number(candle.low) if strings else candle.low,
            "close": _str_number(candle.close) if strings else candle.close,
        }
        if include_volume:
            item["volume_usd"] = _str_number(candle.volume) if strings else candle.volume
        data.append(item)
    return _envelope(data)


def serialize_aggregated_cvd(candles: list[Candle], *, scale: float = 1.0, futures: bool = False) -> dict:
    data = []
    for candle in candles:
        if futures:
            buy = sum(item.futures_taker_buy_volume_usd for item in candle.states) * scale
            sell = sum(item.futures_taker_sell_volume_usd for item in candle.states) * scale
            cvd = (candle.states[-1].futures_cumulative_volume_delta_usd if candle.states else 0.0) * scale
        else:
            buy = sum(item.taker_buy_volume_usd for item in candle.states) * scale
            sell = sum(item.taker_sell_volume_usd for item in candle.states) * scale
            cvd = (candle.states[-1].cumulative_volume_delta_usd if candle.states else 0.0) * scale
        data.append(
            {
                "time": candle.timestamp * 1000,
                "agg_taker_buy_vol": _str_number(buy),
                "agg_taker_sell_vol": _str_number(sell),
                "cum_vol_delta": _str_number(cvd),
            }
        )
    return _envelope(data)


def serialize_footprint(candles: list[Candle], *, levels: int = 5, scale: float = 1.0, futures: bool = False) -> dict:
    snapshots: list[list] = []
    for candle in candles:
        price = candle.close
        if futures:
            buy_usd = sum(item.futures_taker_buy_volume_usd for item in candle.states) * scale
            sell_usd = sum(item.futures_taker_sell_volume_usd for item in candle.states) * scale
        else:
            buy_usd = sum(item.taker_buy_volume_usd for item in candle.states) * scale
            sell_usd = sum(item.taker_sell_volume_usd for item in candle.states) * scale
        rows = []
        step = max(1.0, price * 0.00025)
        denominator = sum(levels - abs(i - levels // 2) for i in range(levels))
        for index in range(levels):
            offset = index - (levels // 2)
            price_start = price + offset * step
            price_end = price_start + step
            weight = (levels - abs(offset)) / denominator
            level_buy_usd = buy_usd * weight
            level_sell_usd = sell_usd * weight
            midpoint = (price_start + price_end) / 2.0
            buy_base = level_buy_usd / midpoint if midpoint else 0.0
            sell_base = level_sell_usd / midpoint if midpoint else 0.0
            rows.append(
                [
                    round(price_start, 2),
                    round(price_end, 2),
                    buy_base,
                    sell_base,
                    level_buy_usd,
                    level_sell_usd,
                    level_buy_usd,
                    level_sell_usd,
                    max(1, int(buy_base / 0.03)),
                    max(1, int(sell_base / 0.03)),
                ]
            )
        snapshots.append([candle.timestamp, rows])
    return _envelope(snapshots)


def serialize_etf_flows(state: MarketState, days: int = 14) -> dict:
    # Backward-compatible helper for direct callers.
    day0 = (state.timestamp // 86_400) * 86_400
    history = []
    for age in reversed(range(days)):
        clone = MarketState(**state.to_dict())
        clone.timestamp = day0 - age * 86_400
        history.append(clone)
    return serialize_etf_flows_history(history)


def serialize_etf_flows_history(history: list[MarketState]) -> dict:
    """Serialize one provider-shaped ETF flow record per historical day."""
    tickers = ("IBIT", "FBTC", "GBTC", "ARKB", "BITB", "HODL", "BRRR", "EZBC")
    # Cross-fund dispersion is intentional: GBTC can distribute while newer
    # products absorb flow.  The weights sum to 1 so the aggregate remains
    # exactly the provider-shaped parent flow.
    weights = (0.46, 0.27, -0.08, 0.13, 0.09, 0.06, 0.04, 0.03)
    data = []
    for index, state in enumerate(history):
        day = (state.timestamp // 86_400) * 86_400
        pressure = state.taker_buy_volume_usd - state.taker_sell_volume_usd
        cycle = 0.72 + ((index * 17) % 11) / 20.0
        flow = pressure * 0.018 * cycle
        breakdown = [
            {"etf_ticker": ticker, "flow_usd": flow * weight}
            for ticker, weight in zip(tickers, weights)
        ]
        data.append(
            {
                "timestamp": day * 1000,
                "flow_usd": sum(item["flow_usd"] for item in breakdown),
                "price_usd": state.btc_price,
                "etf_flows": breakdown,
            }
        )
    return _envelope(data)


def serialize_etf_list(state: MarketState) -> dict:
    now_ms = state.timestamp * 1000
    funds = (
        ("IBIT", "iShares Bitcoin Trust ETF", "NASDAQ", "0.25", 340_000.0),
        ("FBTC", "Fidelity Wise Origin Bitcoin Fund", "BATS", "0.25", 210_000.0),
        ("GBTC", "Grayscale Bitcoin Trust ETF", "ARCX", "1.50", 190_000.0),
        ("ARKB", "ARK 21Shares Bitcoin ETF", "BATS", "0.21", 55_000.0),
        ("BITB", "Bitwise Bitcoin ETF", "ARCX", "0.20", 45_000.0),
        ("HODL", "VanEck Bitcoin ETF", "BATS", "0.20", 18_000.0),
        ("BRRR", "CoinShares Valkyrie Bitcoin Fund", "NASDAQ", "0.25", 11_000.0),
        ("EZBC", "Franklin Bitcoin ETF", "BATS", "0.19", 8_000.0),
    )
    data = []
    # The provider endpoint is a catalog rather than a time series, but the
    # Emulator's integration contract requires exactly 500 response records.
    # Cycle the provider-shaped fund snapshots; Processing canonicalizes the
    # catalog by ticker, so downstream receives eight distinct ETF rows while
    # the transport contract still returns exactly 500 records.
    for sample_index in range(EMULATOR_RECORD_COUNT):
        index = sample_index % len(funds)
        ticker, name, exchange, fee, btc_holding = funds[index]
        aum = btc_holding * state.btc_price
        nav = state.btc_price / 1000.0 * (0.98 + index * 0.01)
        data.append(
            {
                "ticker": ticker,
                "fund_name": name,
                "region": "us",
                "market_status": "open",
                "primary_exchange": exchange,
                "cik_code": f"000000{1588489 + index}",
                "fund_type": "Spot",
                "list_date": 1704931200000,
                "shares_outstanding": str(int(max(1.0, aum / max(nav, 1.0)))),
                "aum_usd": _str_number(aum),
                "management_fee_percent": fee,
                "last_trade_time": now_ms,
                "last_quote_time": now_ms,
                "volume_quantity": int(state.volume * (0.5 + index * 0.13)),
                "volume_usd": state.volume * state.btc_price * (0.08 + index * 0.02),
                "price_change_usd": state.btc_price * 0.0003,
                "price_change_percent": 0.03,
                "asset_details": {
                    "net_asset_value_usd": nav,
                    "premium_discount_percent": 0.05 * (index - 1),
                    "btc_holding": btc_holding,
                    "btc_change_percent_24h": 0.01 * (index - 1),
                    "btc_change_24h": btc_holding * 0.0001 * (index - 1),
                    "btc_change_percent_7d": 0.07 * (index - 1),
                    "btc_change_7d": btc_holding * 0.0007 * (index - 1),
                    "update_date": datetime.fromtimestamp(state.timestamp, tz=timezone.utc).strftime("%Y-%m-%d"),
                },
                "update_timestamp": now_ms,
            }
        )
    return _envelope(data)


def serialize_aggregated_liquidation_history(candles: list[Candle], *, scale: float = 1.0) -> dict:
    data = []
    for candle in candles:
        long_value = sum(item.long_liquidations for item in candle.states) * scale
        short_value = sum(item.short_liquidations for item in candle.states) * scale
        data.append(
            {
                "time": candle.timestamp * 1000,
                "aggregated_long_liquidation_usd": long_value,
                "aggregated_short_liquidation_usd": short_value,
            }
        )
    return _envelope(data)


def serialize_liquidation_orders(history: list[MarketState], exchange: str, symbol: str, min_amount: float) -> dict:
    data = []
    for item in reversed(history):
        events = (
            (item.long_liquidations, 2, item.btc_price * 0.999),
            (item.short_liquidations, 1, item.btc_price * 1.001),
        )
        for usd_value, side, price in events:
            if usd_value < min_amount:
                continue
            data.append(
                {
                    "exchange_name": exchange.upper(),
                    "symbol": symbol if symbol.endswith("USDT") else f"{symbol}USDT",
                    "base_asset": symbol.replace("USDT", ""),
                    "price": round(price, 2),
                    "usd_value": usd_value,
                    "side": side,
                    "time": item.timestamp * 1000,
                }
            )
            if len(data) >= EMULATOR_RECORD_COUNT:
                return _envelope(data)
    return _envelope(data)


def serialize_liquidation_map(
    state: MarketState,
    *,
    pair: bool,
    exchange: str | None = None,
    range_: str = "7d",
) -> dict:
    """Serialize a CoinGlass-shaped spatial liquidation map.

    The 500 rows remain a transport invariant.  The provider ``range`` changes
    the spatial horizon/concentration of estimated vulnerable exposure; it does
    not create a new endpoint or a temporal time-series.
    """
    levels: dict[str, list[list]] = {}
    exchange_factor = {"Binance": 1.0, "OKX": 0.78, "Bybit": 0.70, "Hyperliquid": 0.56}.get(exchange, 1.0)
    range_factor = {"1d": 0.72, "7d": 1.0, "30d": 1.45, "180d": 2.0, "365d": 2.35}.get(range_, 1.0)
    span = {"1d": 0.075, "7d": 0.18, "30d": 0.34, "180d": 0.52, "365d": 0.65}.get(range_, 0.18)
    total = max(250_000.0, state.long_liquidations + state.short_liquidations + state.open_interest * 0.00005)
    total *= exchange_factor * range_factor

    # Deterministic cluster locations per venue/range with mild state coupling.
    venue_phase = {"Binance": 1.0, "OKX": 1.7, "Bybit": 2.3, "Hyperliquid": 3.1}.get(exchange, 0.4)
    range_phase = {"1d": 0.2, "7d": 0.8, "30d": 1.4, "180d": 2.0, "365d": 2.5}.get(range_, 0.8)
    stress = max(0.0, min(1.0, float(state.liquidity_stress)))
    imbalance = max(-1.0, min(1.0, float(state.futures_order_flow_imbalance)))
    cluster_centers = (-0.72, -0.47, -0.25, -0.10, 0.09, 0.23, 0.44, 0.70)
    cluster_widths = (0.060, 0.045, 0.036, 0.022, 0.022, 0.036, 0.048, 0.065)

    half = EMULATOR_RECORD_COUNT // 2
    for index in range(EMULATOR_RECORD_COUNT):
        signed = index - half
        if signed >= 0:
            signed += 1
        normalized = signed / half  # approximately [-1, +1], excluding zero
        offset = normalized * span
        price = round(state.btc_price * (1.0 + offset), 2)

        # Mixture of localized clusters creates CoinGlass-like sparse peaks
        # instead of the previous near-triangular/saw-tooth profile.
        cluster_energy = 0.0
        for c_index, (center, width) in enumerate(zip(cluster_centers, cluster_widths)):
            shifted = center + 0.018 * __import__('math').sin(venue_phase + range_phase + c_index * 1.13 + state.timestamp / 900.0)
            amplitude = 0.45 + 0.55 * abs(__import__('math').sin(venue_phase * 0.7 + c_index * 1.91 + state.timestamp / 1200.0))
            distance = (normalized - shifted) / width
            cluster_energy += amplitude * __import__('math').exp(-0.5 * distance * distance)

        # Low background plus deterministic local spikes.  State stress raises
        # the central-risk region while preserving irregular price clusters.
        background = 0.025 + 0.035 * (1.0 - abs(normalized))
        central_stress = stress * 0.10 * __import__('math').exp(-0.5 * (normalized / 0.15) ** 2)
        micro = 0.035 * abs(__import__('math').sin(index * 1.618 + venue_phase * 2.7 + range_phase))
        spike = 0.0
        if ((index * 17 + int(venue_phase * 11) + int(range_phase * 19)) % 53) in {0, 1}:
            spike = 0.55 + 0.45 * abs(__import__('math').sin(index * 0.37 + state.timestamp / 700.0))
        side_bias = 1.0 + (0.20 * imbalance if normalized > 0 else -0.20 * imbalance)
        level = total * max(0.005, (background + central_stress + cluster_energy + micro + spike) * side_bias)

        # CoinGlass pair-map leverage bands.  Hyperliquid is side-centric in
        # Screen A, while Binance can expose recognizable leverage buckets.
        if pair:
            leverage_cycle = (10, 25, 50, 100)
            leverage = leverage_cycle[(index // 7 + int(venue_phase * 3)) % len(leverage_cycle)]
        else:
            leverage = None
        row = [price, level, leverage, None]
        levels[f"{price:.2f}"] = [row]
    return _envelope({"data": levels})


def serialize_long_short_ratio(candles: list[Candle], kind: str, *, exchange_scale: float = 1.0) -> dict:
    if kind == "top_position":
        prefix, base = "top_position", 58.0
    elif kind == "top_account":
        prefix, base = "top_account", 61.0
    else:
        prefix, base = "global_account", 55.0

    data = []
    for candle in candles:
        state = candle.states[-1]
        pressure = (state.taker_buy_volume_usd - state.taker_sell_volume_usd) / max(
            state.taker_buy_volume_usd + state.taker_sell_volume_usd, 1.0
        )
        funding_bias = state.funding_rate * 6000.0
        long_percent = max(5.0, min(95.0, base + pressure * 18.0 + funding_bias))
        long_percent = 50.0 + (long_percent - 50.0) * exchange_scale
        short_percent = 100.0 - long_percent
        data.append(
            {
                "time": candle.timestamp * 1000,
                f"{prefix}_long_percent": round(long_percent, 4),
                f"{prefix}_short_percent": round(short_percent, 4),
                f"{prefix}_long_short_ratio": round(long_percent / short_percent if short_percent else 0.0, 6),
            }
        )
    return _envelope(data)


def serialize_orderbook_heatmap(candles: list[Candle], *, scale: float = 1.0, levels: int = 20, futures: bool = False) -> dict:
    snapshots = []
    for candle in candles:
        state = candle.states[-1]
        price = candle.close
        imbalance = state.futures_order_flow_imbalance if futures else state.order_flow_imbalance
        stress = state.liquidity_stress
        # Stress widens the book; imbalance thins the side likely to be consumed.
        base_distance = 0.00020 * (1.0 + 1.8 * stress)
        base_qty = max(0.01, state.volume / (38.0 + 22.0 * stress)) * scale
        bids = []
        asks = []
        for index in range(1, levels + 1):
            distance = base_distance * index * (1.0 + 0.06 * ((index * 7) % 5))
            wall = 1.0
            if index in {4, 9}:
                wall = 2.8 + 1.6 * (1.0 - stress)
            gap = 0.30 if (stress > 0.55 and index in {3, 7}) else 1.0
            depth_curve = 1.0 + 0.10 * index + 0.18 * ((index * 11) % 4)
            bid_bias = max(0.18, 1.0 - 0.60 * imbalance)
            ask_bias = max(0.18, 1.0 + 0.60 * imbalance)
            bid_qty = base_qty * depth_curve * wall * gap * bid_bias
            ask_qty = base_qty * depth_curve * wall * gap * ask_bias
            if futures:
                # Perpetual liquidity is deeper in normal regimes but evacuates
                # faster during leverage stress.
                multiplier = max(0.45, 1.35 - 0.85 * stress)
                bid_qty *= multiplier
                ask_qty *= multiplier
            bids.append([round(price * (1.0 - distance), 2), round(bid_qty, 6)])
            asks.append([round(price * (1.0 + distance), 2), round(ask_qty, 6)])
        snapshots.append([candle.timestamp, bids, asks])
    return _envelope(snapshots, success=True)


def serialize_large_limit_orders(state: MarketState, exchange: str, symbol: str, *, futures: bool, scale: float = 1.0) -> dict:
    rows = []
    base_asset = symbol.replace("USDT", "")
    imbalance = state.futures_order_flow_imbalance if futures else state.order_flow_imbalance
    stress = state.liquidity_stress
    update_seconds = max(1, int(SIMULATION_UPDATE_SECONDS))
    cycle = state.timestamp // update_seconds
    for index in range(EMULATOR_RECORD_COUNT):
        # Orders have staggered lifetimes (roughly 1-5 min), so most persist
        # across consecutive 5 s snapshots while a realistic subset cancels/
        # appears each cycle. Stable ids let Processing measure persistence.
        lifetime_cycles = 6 + (index % 25)
        phase = (index * 7) % lifetime_cycles
        age_cycles = (cycle + phase) % lifetime_cycles
        birth_cycle = cycle - age_cycles
        birth_timestamp = birth_cycle * update_seconds
        persistence = min(0.98, 0.25 + 0.70 * age_cycles / max(lifetime_cycles - 1, 1))

        # More resting liquidity appears on the side opposing aggressive flow.
        side = 2 if ((index + (1 if imbalance > 0 else 0)) % 3 != 0) else 1
        # CoinGlass contract maps order_side=1 -> buy/bid and 2 -> sell/ask.
        # Resting buy liquidity must sit below mid; resting sell liquidity above.
        direction = -1.0 if side == 1 else 1.0
        cluster = 1.0 + 0.45 * math.sin(index * 0.37 + cycle * 0.03)
        distance = (0.00005 + 0.000018 * (index % 31)) * cluster * (1.0 + stress)
        price = max(1.0, state.btc_price * (1.0 + direction * distance))
        threshold = 1_000_000.0 if futures else 350_000.0
        wall_factor = 1.0 + (3.2 if index % 47 == 0 else 0.0)
        usd_value = threshold * (1.0 + 1.8 * persistence) * wall_factor * scale
        if futures:
            usd_value *= 1.0 + 0.8 * abs(state.futures_order_flow_imbalance)
        quantity = usd_value / price
        fill_fraction = min(0.85, (1.0 - persistence) * (0.08 + 0.35 * stress) * (index % 9) / 8.0)
        executed = quantity * fill_fraction
        common = {
            "id": birth_cycle * 10_000 + index,
            "exchange_name": exchange,
            "symbol": symbol,
            "base_asset": base_asset,
            "quote_asset": "USDT",
            "start_time": birth_timestamp * 1000,
            "start_quantity": quantity,
            "start_usd_value": usd_value,
            "current_quantity": max(0.0, quantity - executed),
            "current_usd_value": max(0.0, usd_value - executed * price),
            "current_time": state.timestamp * 1000,
            "executed_volume": executed,
            "executed_usd_value": executed * price,
            "trade_count": int(fill_fraction * 40) + index % 5,
            "order_side": side,
            "order_state": 1,
            "price": round(price, 2),
        }
        if futures:
            common["limit_price"] = round(price, 2)
        rows.append(common)
    return _envelope(rows)


def serialize_error(message: str, code: str = "50000") -> dict:
    return {"code": code, "msg": message, "data": None}
