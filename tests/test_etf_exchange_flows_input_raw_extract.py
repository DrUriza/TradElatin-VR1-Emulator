from copy import deepcopy

import pytest

from etf_exchange_flows_helpers import Fetcher, NOW
from processing_signals.input.etf_exchange_flows.etf_exchange_flows_data_raw_extract import (
    ENDPOINT_SPECS, build_coinglass_params, build_cryptoquant_params, build_etf_exchange_flows_fetch_plan,
    build_glassnode_params, extract_etf_exchange_flows_raw,
)


def test_bootstrap_and_incremental_plans_are_deterministic_and_primary_only():
    bootstrap = build_etf_exchange_flows_fetch_plan(mode="bootstrap", exchange_scope="all_exchange")
    hourly = build_etf_exchange_flows_fetch_plan(mode="incremental", exchange_scope="all_exchange",
                                                  refresh_hourly=True, refresh_slow=False)
    slow = build_etf_exchange_flows_fetch_plan(mode="incremental", exchange_scope="all_exchange",
                                                refresh_hourly=True, refresh_slow=True)
    noop = build_etf_exchange_flows_fetch_plan(mode="incremental", exchange_scope="all_exchange",
                                                refresh_hourly=False, refresh_slow=False)
    assert len(bootstrap) == 8
    assert len(hourly) == 4
    assert len(slow) == 4
    assert noop == []
    assert {x["endpoint_id"] for x in hourly} == {"bitcoin_etf_flows", "exchange_inflow", "exchange_outflow", "exchange_reserve"}
    assert not any(x["endpoint_id"] in {"exchange_balance_list", "exchange_balance_chart", "exchange_netflow"} for x in hourly)
    assert all(x["provider"] != "glassnode" for x in bootstrap)
    assert next(x for x in bootstrap if x["provider"] == "cryptoquant" and x["variant"] == "day")["params"]["limit"] == 730


def test_params_are_provider_specific_and_secret_free():
    assert build_coinglass_params("exchange_balance_list") == {"symbol": "BTC"}
    assert build_cryptoquant_params(exchange_scope="coinbase", window="hour", limit=10)["exchange"] == "coinbase"
    assert build_glassnode_params(interval="1h") == {"a": "BTC", "i": "1h", "f": "json"}


def test_fetcher_is_injected_and_raw_bodies_are_deep_copied():
    fetcher = Fetcher()
    raw = extract_etf_exchange_flows_raw(fetcher=fetcher, mode="bootstrap", exchange_scope="all_exchange", now=NOW)
    original = deepcopy(raw["raw"]["coinglass"]["bitcoin_etf_flows"]["response"])
    fetcher.calls[0]["params"]["changed"] = True
    assert raw["raw"]["coinglass"]["bitcoin_etf_flows"]["response"] == original
    assert all(not ({"headers", "token", "api_key"} & set(call["params"])) for call in fetcher.calls)


def test_secondary_lists_and_isolated_redacted_failure():
    fetcher = Fetcher(fail="bitcoin_etf_list")
    raw = extract_etf_exchange_flows_raw(fetcher=fetcher, mode="bootstrap", exchange_scope="all_exchange",
        include_secondary=True, now=NOW)
    assert isinstance(raw["raw"]["glassnode"]["exchange_inflow"]["24h"]["response"], list)
    assert raw["raw"]["coinglass"]["bitcoin_etf_list"]["error"] == "provider_error_redacted"
    assert raw["raw"]["coinglass"]["bitcoin_etf_flows"]["status"] == "ok"


def test_invalid_recovery_is_rejected_before_fetch():
    fetcher = Fetcher()
    with pytest.raises(ValueError):
        extract_etf_exchange_flows_raw(fetcher=fetcher, mode="recovery", exchange_scope="all_exchange", now=NOW,
            recovery_requests=[{"provider": "cryptoquant", "endpoint_id": "exchange_inflow", "window": "week"}])
    assert fetcher.calls == []
