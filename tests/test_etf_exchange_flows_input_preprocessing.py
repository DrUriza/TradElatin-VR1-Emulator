from copy import deepcopy
import json

from etf_exchange_flows_helpers import Fetcher, NOW
from processing_signals.input.etf_exchange_flows.etf_exchange_flows_data_raw_preprocessing import run_etf_exchange_flows_input


def _run(**kwargs):
    return run_etf_exchange_flows_input(fetcher=kwargs.pop("fetcher", Fetcher()), requested_mode=kwargs.pop("mode", "bootstrap"),
        exchange_scope="all_exchange", now=kwargs.pop("now", NOW), **kwargs)


def test_all_primary_schemas_normalize_without_cross_endpoint_fields():
    output = _run()
    datasets = output["datasets"]
    assert datasets["etf_flows_daily"][0]["timestamp"] == NOW
    assert datasets["etf_fund_flows_daily"][0]["ticker"] == "GBTC"
    assert datasets["etf_funds_snapshot"][0]["aum_usd"] == 100.5
    assert datasets["etf_premium_discount_daily"][0]["premium_discount_percent"] == -1
    assert datasets["exchange_balances_history"] == []  # retired CoinGlass balance chart is not polled
    assert set(datasets["exchange_inflow"]["hour"][0]) >= {"inflow_total", "inflow_top10", "inflow_mean"}
    assert "outflow_total" not in datasets["exchange_inflow"]["hour"][0]
    json.dumps(output, allow_nan=False)


def test_null_is_preserved_and_structured_glassnode_value_is_preserved():
    output = _run(include_secondary=True)
    assert output["datasets"]["exchange_inflow"]["day"][0]["inflow_total"] is None
    record = output["datasets"]["secondary_sources"]["glassnode"]["exchange_balance"]["24h"][0]
    assert record["value"] is None and record["value_raw"] == {"nested": 1}
    assert any("structured_glassnode_value" in warning for warning in output["quality"]["warnings"])


def test_incremental_upsert_replaces_timestamp_and_preserves_history_and_inputs():
    existing = _run()
    before = deepcopy(existing)
    incoming = _run(fetcher=Fetcher(NOW + 3600), mode="incremental", now=NOW + 3600, existing_contract=existing)
    rows = incoming["datasets"]["etf_flows_daily"]
    assert existing == before and [row["timestamp"] for row in rows] == [NOW, NOW + 3600]


def test_empty_or_failed_response_does_not_erase_history():
    existing = _run()
    empty = _run(fetcher=Fetcher(NOW + 3600, empty=True), mode="incremental", now=NOW + 3600, existing_contract=existing)
    failed = _run(fetcher=Fetcher(NOW + 3600, fail="bitcoin_etf_flows"), mode="incremental",
                  now=NOW + 3600, existing_contract=existing)
    assert empty["datasets"]["etf_flows_daily"] == existing["datasets"]["etf_flows_daily"]
    assert failed["datasets"]["etf_flows_daily"] == existing["datasets"]["etf_flows_daily"]


def test_input_boundary_excludes_processing_and_hmi_fields():
    text = json.dumps(_run())
    forbidden = ("flow_btc", "cumulative_etf_net_flow", "total_etf_aum", "total_exchange_balance",
                 "exchange_flow_pressure", '"signal"', '"confidence"', '"kpis"', '"charts"', '"widgets"', '"screen"')
    assert not any(token in text for token in forbidden)
