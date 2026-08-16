from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping


SP_SCHEMA_VERSION = "1.9.0"
SP_TEMPLATE_PATH = Path(__file__).with_name("prices_screen_sp_v1_9.json")
_MISSING = object()


def _template() -> dict[str, Any]:
    return json.loads(SP_TEMPLATE_PATH.read_text(encoding="utf-8"))


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (dict, list))


def _project(reference: Any, candidate: Any = _MISSING) -> Any:
    """Project candidate values onto the exact SP key/nesting structure.

    Extra candidate keys are deliberately discarded. Missing keys retain the
    SP contract default, which is appropriate for static presentation policy
    fields. Dynamic Prices fields are supplied by the regular contract builder
    before this projection.
    """
    if isinstance(reference, dict):
        source = candidate if isinstance(candidate, Mapping) else {}
        return {
            key: _project(value, source.get(key, _MISSING))
            for key, value in reference.items()
        }
    if isinstance(reference, list):
        if candidate is _MISSING:
            return deepcopy(reference)
        if not isinstance(candidate, list):
            return deepcopy(reference)
        if not reference:
            return deepcopy(candidate)
        if all(_is_scalar(item) for item in reference):
            return deepcopy(candidate)
        if not candidate:
            return []

        # Static contract lists (KPIs, indicator rows, thresholds, badges, etc.)
        # have heterogeneous nested shapes.  Match by semantic identity instead
        # of projecting every item against reference[0].  Dynamic homogeneous
        # lists (OHLC records, time-series records) naturally fall back to the
        # positional/prototype reference shape.
        identity_keys = (
            "metric_id", "kpi_id", "widget_id", "chart_id", "table_id",
            "badge_id", "id", "role", "family", "group", "indicator_id",
            "first_series", "event_type",
        )

        def reference_for(item: Any, index: int) -> Any:
            if isinstance(item, Mapping):
                for key in identity_keys:
                    value = item.get(key, _MISSING)
                    if value is _MISSING:
                        continue
                    for ref_item in reference:
                        if isinstance(ref_item, Mapping) and ref_item.get(key, _MISSING) == value:
                            return ref_item
            if index < len(reference):
                return reference[index]
            return reference[0]

        return [_project(reference_for(item, index), item) for index, item in enumerate(candidate)]
    if candidate is _MISSING:
        return deepcopy(reference)
    return deepcopy(candidate)


def _project_dynamic_events(reference_events: Mapping[str, Any], candidate_events: Mapping[str, Any]) -> dict[str, Any]:
    reference_by_id = reference_events.get("by_id", {}) if isinstance(reference_events, Mapping) else {}
    candidate_by_id = candidate_events.get("by_id", {}) if isinstance(candidate_events, Mapping) else {}
    prototypes: dict[tuple[str | None, str | None], Mapping[str, Any]] = {}
    for event in reference_by_id.values():
        if isinstance(event, Mapping):
            prototypes.setdefault((event.get("event_type"), event.get("event_group")), event)
            prototypes.setdefault((event.get("event_type"), None), event)

    output: dict[str, Any] = {}
    for uid, event in candidate_by_id.items():
        if not isinstance(event, Mapping):
            continue
        prototype = prototypes.get((event.get("event_type"), event.get("event_group"))) or prototypes.get((event.get("event_type"), None))
        output[str(uid)] = _project(prototype, event) if prototype is not None else deepcopy(dict(event))
    return output


def align_prices_contract_to_sp_v1_9(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Return a Prices contract with the exact final Screen-SP structure."""
    reference = _template()
    aligned = _project(reference, dict(candidate))

    # Event IDs and timestamps are runtime data.  Preserve the candidate event
    # registry while projecting each event onto the SP event shape; never copy
    # fixture event IDs/values from the SP template into runtime output.
    candidate_events = candidate.get("events", {}) if isinstance(candidate, Mapping) else {}
    if isinstance(aligned.get("events"), dict) and isinstance(candidate_events, Mapping):
        aligned["events"]["by_id"] = _project_dynamic_events(reference.get("events", {}), candidate_events)

    aligned["family"] = "prices_ohlcv"
    aligned["screen"] = "prices"
    aligned["schema_version"] = SP_SCHEMA_VERSION

    context = aligned.get("context", {})
    context["default_market"] = "general"
    context["available_markets"] = ["general"]
    context["price_role"] = "canonical_general_reference"
    context["market_scope"] = "single_general_market"
    context["price_construction"] = "direct_general_reference_series"
    # Never describe a live provider run as a synthetic fixture merely because
    # the SP reference used a calibrated visual fixture.
    if not bool(context.get("is_demo", False)):
        context["synthetic_fixture"] = False
        context["fixture_as_of_timestamp"] = None
        context["fixture_as_of_iso"] = None
        context["realism_refactor_version"] = "runtime_provider_v1"
        context["realism_note"] = "runtime provider data; general is canonical CoinGlass Spot"
    aligned["context"] = context

    selectors = aligned.get("selectors", {})
    if isinstance(selectors.get("market"), dict):
        selectors["market"].update({"selected": "general", "options": ["general"], "status": "fixed", "visible": False})
    aligned["selectors"] = selectors

    # VR1-final makes Screen B canonical at the root.  Promote the already
    # computed package; retain the chart copy only as an explicit legacy mirror.
    chart_analysis = aligned.get("charts", {}).get("ohlcv", {}).get("technical_fundamental_analysis")
    if isinstance(chart_analysis, Mapping):
        root_analysis = deepcopy(dict(chart_analysis))
        root_analysis.update({
            "canonical_location": "technical_analysis",
            "compatibility_mirror": False,
            "legacy_mirror_paths": ["charts.ohlcv.technical_fundamental_analysis"],
        })
        aligned["technical_analysis"] = root_analysis
        chart_analysis.update({
            "canonical_location": "technical_analysis",
            "compatibility_mirror": True,
            "excluded_from_panel_order": ["price_vs_vwap"],
        })

    # Runtime output must remain strict JSON.
    json.dumps(aligned, allow_nan=False)
    return aligned
