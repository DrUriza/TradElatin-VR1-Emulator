from __future__ import annotations

from pathlib import Path

from processing_signals.classification.classification_pipeline import (
    CLASSIFICATION_FAMILY_HANDLERS,
    FAMILY_ORDER as CLASSIFICATION_ORDER,
)
from processing_signals.classification.contract_builder_pipeline import (
    CONTRACT_BUILDER_FAMILY_HANDLERS,
    FAMILY_ORDER as CONTRACT_BUILDER_ORDER,
)
from processing_signals.input.input_pipeline import INPUT_FAMILY_HANDLERS
from processing_signals.main.etf_exchange_flows.etf_exchange_flows_vertical import (
    DEFAULT_ETF_EXCHANGE_FLOWS_OUTPUT_PATH,
)
from processing_signals.main.main_pipeline import VERTICAL_FAMILY_HANDLERS
from processing_signals.main.runtime_orchestrator import FAMILY_ORDER, SCREEN_FILENAMES
from processing_signals.main.screen_contract_export import CVD_VOLUME_ORDERFLOW_OUTPUT_PATH
from processing_signals.processing.processing_pipeline import PROCESSING_FAMILY_HANDLERS
from processing_signals.runtime.emulator.provider_router import SUPPORTED_FAMILIES as EMULATOR_FAMILIES
from processing_signals.runtime.providers.real_api import SUPPORTED_FAMILIES as REAL_API_FAMILIES


CANONICAL_FAMILY_ORDER = (
    "prices_ohlcv",
    "cvd_volume_orderflow",
    "open_interest_and_funding",
    "etf_exchange_flows",
    "on_chain_miners",
    "volatility_market_regimes",
    "long_short_liquidations",
    "liquidity_microstructure",
)


def test_all_pipeline_registries_use_one_canonical_eight_family_order() -> None:
    assert FAMILY_ORDER == CANONICAL_FAMILY_ORDER
    assert CLASSIFICATION_ORDER == CANONICAL_FAMILY_ORDER
    assert CONTRACT_BUILDER_ORDER == CANONICAL_FAMILY_ORDER
    assert tuple(INPUT_FAMILY_HANDLERS) == CANONICAL_FAMILY_ORDER
    assert tuple(PROCESSING_FAMILY_HANDLERS) == CANONICAL_FAMILY_ORDER
    assert tuple(CLASSIFICATION_FAMILY_HANDLERS) == CANONICAL_FAMILY_ORDER
    assert tuple(CONTRACT_BUILDER_FAMILY_HANDLERS) == CANONICAL_FAMILY_ORDER
    assert tuple(VERTICAL_FAMILY_HANDLERS) == CANONICAL_FAMILY_ORDER
    assert tuple(EMULATOR_FAMILIES) == CANONICAL_FAMILY_ORDER
    assert tuple(REAL_API_FAMILIES) == CANONICAL_FAMILY_ORDER
    assert tuple(SCREEN_FILENAMES) == CANONICAL_FAMILY_ORDER


def test_only_canonical_hmi_publication_path_is_configured() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / "runtime" / "contracts" / "hmi").is_dir()
    assert not (repo_root / "runtime" / "contracts" / "hmi_contract").exists()
    assert "runtime/contracts/hmi/" in CVD_VOLUME_ORDERFLOW_OUTPUT_PATH.as_posix()
    assert "runtime/contracts/hmi/" in DEFAULT_ETF_EXCHANGE_FLOWS_OUTPUT_PATH.as_posix()


def test_runtime_hmis_have_no_retired_provider_or_pending_fixture_labels() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    hmi_root = repo_root / "runtime" / "contracts" / "hmi"
    assert hmi_root.is_dir()
    assert len(list(hmi_root.glob("*.json"))) == 8
    retired_tokens = (
        "API Market /",
        "pending_processing_live_feed",
        "visual_demo_fixture",
    )
    for path in hmi_root.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        for token in retired_tokens:
            assert token not in text, f"{token!r} remains in {path.name}"

_TRACE_FIELDS = {"source_path", "source_paths", "source_features", "calculation_source", "history_source_path"}


def _trace_values(value, location: str = ""):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{location}.{key}" if location else key
            if key in _TRACE_FIELDS:
                values = item if isinstance(item, list) else [item]
                found.extend((child, path) for path in values if isinstance(path, str) and path)
            found.extend(_trace_values(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_trace_values(item, f"{location}[{index}]"))
    return found


def _resolve_trace_tokens(current, tokens) -> bool:
    import re

    if not tokens:
        return True
    if isinstance(current, list):
        return any(_resolve_trace_tokens(item, tokens) for item in current)
    if not isinstance(current, dict):
        return False
    token = tokens[0]
    if token == "*":
        return any(_resolve_trace_tokens(item, tokens[1:]) for item in current.values())
    match = re.fullmatch(r"([^\[]+)\[([^=]+)=([^\]]+)\]", token)
    if match:
        name, key, expected = match.groups()
        items = current.get(name)
        return isinstance(items, list) and any(
            isinstance(item, dict)
            and str(item.get(key)) == expected
            and _resolve_trace_tokens(item, tokens[1:])
            for item in items
        )
    return token in current and _resolve_trace_tokens(current[token], tokens[1:])


def _trace_resolves(path: str, documents: dict[str, dict]) -> bool:
    stage = None
    for candidate in ("input", "processing", "classification", "hmi"):
        prefix = f"{candidate}."
        if path.startswith(prefix):
            stage = candidate
            path = path[len(prefix):]
            break
    tokens = path.split(".") if path else []
    roots = [documents[stage]] if stage else [
        documents["processing"], documents["classification"],
        documents["input"], documents["hmi"],
    ]
    return any(_resolve_trace_tokens(root, tokens) for root in roots)


def test_runtime_hmi_source_paths_resolve_to_persisted_stage_contracts() -> None:
    import json

    repo_root = Path(__file__).resolve().parents[1]
    contracts = repo_root / "runtime" / "contracts"
    for family, screen_name in SCREEN_FILENAMES.items():
        documents = {
            "input": json.loads((contracts / "input" / f"{family}.json").read_text(encoding="utf-8")),
            "processing": json.loads((contracts / "processing" / f"{family}.json").read_text(encoding="utf-8")),
            "classification": json.loads((contracts / "classification" / f"{family}.json").read_text(encoding="utf-8")),
            "hmi": json.loads((contracts / "hmi" / screen_name).read_text(encoding="utf-8")),
        }
        unresolved = [
            (location, path)
            for location, path in _trace_values(documents["hmi"])
            if not _trace_resolves(path, documents)
        ]
        assert not unresolved, f"Unresolved trace paths for {family}: {unresolved[:10]}"
