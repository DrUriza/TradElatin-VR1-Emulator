from copy import deepcopy
import json
import math

import pytest

from etf_exchange_flows_helpers import Fetcher, NOW, provider_body
from processing_signals.input.etf_exchange_flows.etf_exchange_flows_data_raw_extract import (
    ENDPOINT_SPECS,
    extract_etf_exchange_flows_raw,
)
from processing_signals.input.etf_exchange_flows.etf_exchange_flows_data_raw_preprocessing import (
    _timestamp,
    run_etf_exchange_flows_input,
)


class OverrideFetcher(Fetcher):
    def __init__(self, overrides=None, **kwargs):
        super().__init__(**kwargs)
        self.overrides = overrides or {}

    def __call__(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        key = (kwargs["provider"], kwargs["endpoint_id"], kwargs["params"].get("window"))
        if key in self.overrides:
            return deepcopy(self.overrides[key])
        return provider_body(kwargs["provider"], kwargs["endpoint_id"], kwargs["params"], self.timestamp, empty=self.empty)


def _run(fetcher=None, *, existing=None, mode="bootstrap"):
    return run_etf_exchange_flows_input(fetcher=fetcher or Fetcher(), existing_contract=existing,
        requested_mode=mode, exchange_scope="all_exchange", now=NOW)


def _flow_body(children, *, flow=999):
    return {"code": "0", "msg": "success", "data": [{"timestamp": NOW * 1000,
        "flow_usd": flow, "price_usd": 50_000, "etf_flows": children}]}


@pytest.mark.parametrize(("case_id", "children", "reason"), [
    ("ETF-C01", [{"flow_usd": 1}], "etf_ticker_required"),
    ("ETF-C02", [991001], "invalid_etf_flow_child"),
    ("ETF-C03", [{"etf_ticker": "GBTC", "flow_usd": "bad"}], "invalid_number"),
    ("ETF-C03b", [{"etf_ticker": "GBTC", "flow_usd": 1}, {"flow_usd": 2}], "etf_ticker_required"),
])
def test_etf_parent_and_children_are_atomic(case_id, children, reason):
    fetcher = OverrideFetcher({("coinglass", "bitcoin_etf_flows", None): _flow_body(children)})
    output = _run(fetcher)
    assert output["datasets"]["etf_flows_daily"] == []
    assert output["datasets"]["etf_fund_flows_daily"] == []
    rejected = output["invalid_records"]["coinglass"]["bitcoin_etf_flows"]
    assert len(rejected) == 1 and rejected[0]["reason"] == reason
    assert output["quality"]["endpoints"]["coinglass.bitcoin_etf_flows"]["status"] == "invalid"


def test_empty_children_is_a_valid_atomic_parent():
    output = _run(OverrideFetcher({("coinglass", "bitcoin_etf_flows", None): _flow_body([])}))
    assert len(output["datasets"]["etf_flows_daily"]) == 1
    assert output["datasets"]["etf_fund_flows_daily"] == []


def test_etf_c04_invalid_incoming_preserves_existing():
    existing = _run(OverrideFetcher({("coinglass", "bitcoin_etf_flows", None): _flow_body([], flow=100)}))
    existing["context"]["api_refresh_state"]["hourly"] = NOW - 3600
    before = deepcopy(existing)
    incoming = _run(OverrideFetcher({("coinglass", "bitcoin_etf_flows", None): _flow_body([{"flow_usd": 1}])}),
                    existing=existing, mode="incremental")
    assert existing == before
    assert incoming["datasets"]["etf_flows_daily"][0]["flow_usd"] == 100
    assert all(row["flow_usd"] != 999 for row in incoming["datasets"]["etf_flows_daily"])
    quality = incoming["quality"]["endpoints"]["coinglass.bitcoin_etf_flows"]
    assert quality["status"] == "invalid" and quality["records_rejected"] == 1


@pytest.mark.parametrize(("case_id", "recovery_item"), [
    ("ETF-C05", {"provider": "coinglass", "endpoint_id": "bitcoin_etf_flows", "start_time": 20, "end_time": 10}),
    ("ETF-C06", {"provider": "coinglass", "endpoint_id": "bitcoin_etf_flows", "limit": 0}),
    ("ETF-C06b", {"provider": "coinglass", "endpoint_id": "bitcoin_etf_flows", "limit": -1}),
    ("ETF-C06c", {"provider": "coinglass", "endpoint_id": "bitcoin_etf_flows", "limit": True}),
    ("ETF-C06d", {"provider": "coinglass", "endpoint_id": "bitcoin_etf_flows", "start_time": True}),
    ("ETF-C06e", {"provider": "coinglass", "endpoint_id": "bitcoin_etf_flows", "end_time": 1.5}),
    ("ETF-C07", {"provider": "coinglass", "endpoint_id": "bitcoin_etf_flows", "unknown": 1}),
    ("ETF-C07b", {"provider": "coinglass", "endpoint_id": "exchange_reserve"}),
])
def test_invalid_recovery_is_rejected_before_fetch(case_id, recovery_item):
    fetcher = Fetcher()
    with pytest.raises(ValueError):
        extract_etf_exchange_flows_raw(fetcher=fetcher, mode="recovery", exchange_scope="all_exchange",
            now=NOW, recovery_requests=[recovery_item])
    assert fetcher.calls == []


def test_etf_c08_whole_recovery_plan_is_validated_before_fetch():
    fetcher = Fetcher()
    requests = [{"provider": "coinglass", "endpoint_id": "bitcoin_etf_flows"},
                {"provider": "cryptoquant", "endpoint_id": "exchange_inflow", "window": "week"}]
    with pytest.raises(ValueError, match="invalid_cryptoquant_window"):
        extract_etf_exchange_flows_raw(fetcher=fetcher, mode="recovery", exchange_scope="all_exchange",
            now=NOW, recovery_requests=requests)
    assert fetcher.calls == []


def test_valid_recovery_still_fetches_once():
    fetcher = Fetcher()
    extract_etf_exchange_flows_raw(fetcher=fetcher, mode="recovery", exchange_scope="all_exchange", now=NOW,
        recovery_requests=[{"provider": "coinglass", "endpoint_id": "bitcoin_etf_flows"}])
    assert len(fetcher.calls) == 1


def test_etf_c09_c10_timestamp_contract():
    with pytest.raises(ValueError):
        _timestamp(1.5)
    with pytest.raises(ValueError):
        _timestamp(True)
    with pytest.raises(ValueError):
        _timestamp(math.nan)
    with pytest.raises(ValueError):
        _timestamp(math.inf)
    assert _timestamp(1.0) == 1
    assert _timestamp(1_704_931_200_000) == 1_704_931_200
    assert _timestamp(1_704_931_200_123) == 1_704_931_200
    assert _timestamp("2024-01-11T00:00:00.500Z") == 1_704_931_200


def _matrix_body(*, times=None, prices=None, matrix=None, empty=False):
    if empty:
        data = []
    else:
        data = [{"time_list": times if times is not None else [NOW * 1000],
                 "price_list": prices if prices is not None else [50_000],
                 "data_map": matrix if matrix is not None else {"coinbase": [100]}}]
    return {"code": "0", "msg": "success", "data": data}


def test_etf_c16_c19_strict_json_and_family():
    output = _run()
    json.dumps(output, allow_nan=False)
    assert output["family"] == "etf_exchange_flows"


def test_etf_c17_deep_immutability():
    existing = _run()
    before = deepcopy(existing)
    output = _run(existing=existing, mode="incremental")
    output["datasets"]["etf_flows_daily"][0]["flow_usd"] = 991001
    assert existing == before


def test_etf_c18_no_downstream_calculations():
    serialized = json.dumps(_run())
    forbidden = ("flow_btc", "cumulative_etf_net_flow", "exchange_flow_pressure", '"signal"', '"confidence"')
    assert not any(field in serialized for field in forbidden)


