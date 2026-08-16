from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping


SP_SCHEMA_VERSION = "1.5.0"
SP_TEMPLATE_PATH = Path(__file__).with_name("cvd_screen_sp_v1_5.json")
_MISSING = object()


def _template() -> dict[str, Any]:
    return json.loads(SP_TEMPLATE_PATH.read_text(encoding="utf-8"))


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (dict, list))


def _project(reference: Any, candidate: Any = _MISSING) -> Any:
    """Project runtime data onto the exact frozen CVD Screen-SP shape.

    Static presentation policy comes from the SP.  Runtime values, arrays,
    statuses, timestamps and provenance are supplied by the ordinary builder.
    Extra runtime keys are deliberately discarded so the HMI contract remains
    structurally stable.
    """
    if isinstance(reference, dict):
        source = candidate if isinstance(candidate, Mapping) else {}
        return {key: _project(value, source.get(key, _MISSING)) for key, value in reference.items()}
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

        identity_keys = (
            "metric_id", "kpi_id", "widget_id", "chart_id", "table_id",
            "badge_id", "id", "role", "family", "group", "indicator_id",
            "event_type", "event_group", "market", "timeframe", "window",
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


def align_cvd_contract_to_sp_v1_5(candidate: Mapping[str, Any]) -> dict[str, Any]:
    reference = _template()
    aligned = _project(reference, dict(candidate))

    # Runtime event IDs/timestamps must never be copied from the visual fixture.
    candidate_events = candidate.get("events", {}) if isinstance(candidate, Mapping) else {}
    if isinstance(aligned.get("events"), dict) and isinstance(candidate_events, Mapping):
        aligned["events"]["by_id"] = _project_dynamic_events(reference.get("events", {}), candidate_events)
        aligned["events"]["technical_cross_ids"] = list(aligned["events"]["by_id"])

    aligned["schema"] = {
        "id": "trad_elatin.cvd_volume_orderflow.screen.v1",
        "version": SP_SCHEMA_VERSION,
    }

    context = aligned.get("context", {})
    is_demo = bool(context.get("is_demo", False))
    if not is_demo:
        context["synthetic_fixture"] = False
        context["fixture_as_of_timestamp"] = None
        context["fixture_as_of_iso"] = None
        context["realism_refactor_version"] = "runtime_provider_v1"
        context["realism_note"] = (
            "runtime provider data; CVD OHLC is constructed in Processing from real interval flow observations"
        )
    aligned["context"] = context

    json.dumps(aligned, ensure_ascii=False, allow_nan=False)
    return aligned
