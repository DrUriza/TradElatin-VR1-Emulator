from processing_signals.classification.classification_pipeline import CLASSIFICATION_FAMILY_HANDLERS
from processing_signals.input.input_pipeline import INPUT_FAMILY_HANDLERS
from processing_signals.main.main_pipeline import VERTICAL_FAMILY_HANDLERS, run_main_pipeline
from processing_signals.processing.processing_pipeline import PROCESSING_FAMILY_HANDLERS
from processing_signals.runtime.emulator.provider_router import SyntheticProviderRouter

NOW = 1786100400


def test_family_is_registered_in_all_four_pipelines():
    assert "on_chain_miners" in INPUT_FAMILY_HANDLERS
    assert "on_chain_miners" in PROCESSING_FAMILY_HANDLERS
    assert "on_chain_miners" in CLASSIFICATION_FAMILY_HANDLERS
    assert "on_chain_miners" in VERTICAL_FAMILY_HANDLERS


def test_main_pipeline_returns_current_screen_contract():
    args = {"on_chain_miners": {
        "fetcher": SyntheticProviderRouter().for_family("on_chain_miners"),
        "now_timestamp": NOW,
        "input_arguments": {"requested_mode": "bootstrap", "include_screen_extensions": True,
                            "data_mode": "synthetic", "is_demo": True},
    }}
    screen = run_main_pipeline(enabled_families=("on_chain_miners",), family_arguments=args, screens_only=True)["on_chain_miners"]
    assert screen["screen"]["id"] == "on_chain_miners"
    assert screen["schema"]["version"] == "2.0.0"
