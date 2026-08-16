import json

from long_short_liquidations_integration_helpers import SIDE_IDS, run_vertical


def test_runtime_contract_shape_is_ready_for_disk_inspection():
    screen = json.loads(json.dumps(run_vertical()["screen"], allow_nan=False))
    assert screen["family"] == screen["screen_id"] == "long_short_liquidations"
    assert screen["contract_version"] == "1.2.0" and len(screen["kpis"]) == 7
    assert [item["id"] for item in screen["side_panel"]["items"]] == SIDE_IDS
    assert all(isinstance(item, dict) and {"id", "status"} <= item.keys()
               for item in screen["side_panel"]["items"])
