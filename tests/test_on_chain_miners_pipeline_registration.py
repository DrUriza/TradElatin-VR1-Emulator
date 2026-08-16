from processing_signals.classification.classification_pipeline import CLASSIFICATION_FAMILY_HANDLERS
from processing_signals.input.input_pipeline import INPUT_FAMILY_HANDLERS
from processing_signals.main.main_pipeline import VERTICAL_FAMILY_HANDLERS, run_main_pipeline
from processing_signals.processing.processing_pipeline import PROCESSING_FAMILY_HANDLERS
from on_chain_miners_test_helpers import NOW, FakeFetcher


def test_family_is_registered_in_all_four_pipelines():
    assert "on_chain_miners" in INPUT_FAMILY_HANDLERS
    assert "on_chain_miners" in PROCESSING_FAMILY_HANDLERS
    assert "on_chain_miners" in CLASSIFICATION_FAMILY_HANDLERS
    assert "on_chain_miners" in VERTICAL_FAMILY_HANDLERS


def test_main_pipeline_can_return_bundle_or_screen_only():
    arguments = {"on_chain_miners": {"fetcher": FakeFetcher(), "now_timestamp": NOW,
                 "input_arguments": {"requested_mode": "bootstrap", "include_screen_extensions": True}}}
    bundle = run_main_pipeline(enabled_families=("on_chain_miners",), family_arguments=arguments)["on_chain_miners"]
    screen = run_main_pipeline(enabled_families=("on_chain_miners",), family_arguments=arguments,
                               screens_only=True)["on_chain_miners"]
    assert tuple(bundle) == ("input", "processing", "classification", "screen")
    assert screen["screen"]["id"] == "on_chain_miners"
