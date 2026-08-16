from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


def _timestamp(record: Any) -> int | None:
    value = None
    if isinstance(record, Mapping):
        value = next((record[key] for key in ("timestamp", "time", "t") if key in record), None)
    elif isinstance(record, list) and record:
        value = record[0]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = int(value)
    return value // 1000 if value >= 100_000_000_000 else value


class EmulatorOverlay:
    """Persistent provider-shaped observations layered over frozen fixtures."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.path = self.root / "overlay.json"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "version": 0, "observations": []}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if value.get("schema_version") != 1 or not isinstance(value.get("observations"), list):
            raise ValueError("invalid emulator overlay")
        return value

    def _write(self, value: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.root, delete=False, suffix=".tmp") as handle:
                temporary = Path(handle.name)
                json.dump(value, handle, separators=(",", ":"), allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @property
    def version(self) -> int:
        return int(self._load()["version"])

    @property
    def families(self) -> tuple[str, ...]:
        return tuple(sorted({str(item["family"]) for item in self._load()["observations"]}))

    @property
    def maximum_timestamp(self) -> int | None:
        values = [_timestamp(item["record"]) for item in self._load()["observations"]]
        return max((value for value in values if value is not None), default=None)

    @property
    def source_timeframes(self) -> tuple[str, ...]:
        return tuple(sorted({str(item["selectors"]["interval"]) for item in self._load()["observations"]
                            if "interval" in item["selectors"]}))

    def append_observation(self, *, family: str, provider: str, endpoint_id: str,
                           selectors: Mapping[str, Any], record: Any) -> int:
        value = self._load()
        value["observations"].append({"family": family, "provider": provider, "endpoint_id": endpoint_id,
                                      "selectors": dict(selectors), "record": deepcopy(record)})
        value["version"] = int(value["version"]) + 1
        self._write(value)
        return int(value["version"])

    def revise_observation(self, *, family: str, provider: str, endpoint_id: str,
                           selectors: Mapping[str, Any], timestamp: int, record: Any) -> int:
        value = self._load()
        matches = [item for item in value["observations"] if item["family"] == family and item["provider"] == provider
                   and item["endpoint_id"] == endpoint_id and item["selectors"] == dict(selectors)
                   and _timestamp(item["record"]) == int(timestamp)]
        if len(matches) != 1:
            raise ValueError("overlay revision requires exactly one existing observation")
        matches[0]["record"] = deepcopy(record)
        value["version"] = int(value["version"]) + 1
        self._write(value)
        return int(value["version"])

    def clear_overlay(self) -> None:
        value = self._load()
        self._write({"schema_version": 1, "version": int(value["version"]) + 1, "observations": []})

    def records_for(self, *, family: str, provider: str, endpoint_id: str,
                    params: Mapping[str, Any]) -> list[Any]:
        result = []
        for item in self._load()["observations"]:
            if (item["family"] != family or item["provider"] != provider or item["endpoint_id"] != endpoint_id):
                continue
            if any(params.get(key) != expected for key, expected in item["selectors"].items()):
                continue
            timestamp = _timestamp(item["record"])
            start = params.get("start_time", params.get("start"))
            end = params.get("end_time", params.get("end"))
            if start is not None and timestamp is not None and timestamp < (int(start) // 1000 if int(start) >= 100_000_000_000 else int(start)):
                continue
            if end is not None and timestamp is not None and timestamp > (int(end) // 1000 if int(end) >= 100_000_000_000 else int(end)):
                continue
            result.append(deepcopy(item["record"]))
        return result


def merge_overlay_response(response: Any, records: list[Any]) -> Any:
    if not records:
        return response
    output = deepcopy(response)
    if isinstance(output, dict) and isinstance(output.get("data"), list):
        rows = output["data"]
    elif isinstance(output, list):
        rows = output
    else:
        raise ValueError("overlay cannot merge unsupported provider envelope")
    by_timestamp = {_timestamp(row): row for row in rows if _timestamp(row) is not None}
    timeless = [row for row in rows if _timestamp(row) is None]
    for record in records:
        timestamp = _timestamp(record)
        if timestamp is None:
            timeless.append(record)
        else:
            by_timestamp[timestamp] = record
    rows[:] = timeless + [by_timestamp[key] for key in sorted(by_timestamp)]
    return output
