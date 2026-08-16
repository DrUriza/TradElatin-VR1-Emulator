from copy import deepcopy

from processing_signals.main.main_pipeline import run_main_pipeline
from long_short_liquidations_integration_helpers import REFERENCE, vertical_arguments


def test_main_pipeline_routes_full_and_screen_only_outputs():
    arguments = vertical_arguments(REFERENCE)
    full = run_main_pipeline(enabled_families=("long_short_liquidations",),
        family_arguments={"long_short_liquidations": arguments})["long_short_liquidations"]
    screen = run_main_pipeline(enabled_families=("long_short_liquidations",),
        family_arguments={"long_short_liquidations": vertical_arguments(REFERENCE)}, screens_only=True)["long_short_liquidations"]
    assert set(full) == {"input", "processing", "classification", "screen"}
    assert screen["stage"] == "screen_contract_final" and "input" not in screen


def test_main_pipeline_routes_previous_state_without_mutation():
    first = run_main_pipeline(enabled_families=("long_short_liquidations",),
        family_arguments={"long_short_liquidations": vertical_arguments(REFERENCE)})["long_short_liquidations"]
    before = deepcopy(first)
    args = vertical_arguments(REFERENCE + 3600, mode="incremental")
    second = run_main_pipeline(enabled_families=("long_short_liquidations",),
        family_arguments={"long_short_liquidations": args}, previous_state={"long_short_liquidations": first})
    assert first == before and second["long_short_liquidations"]["screen"] != first["screen"]
