from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from .policies import endpoint_policy


SCHEMA_VERSION = 1
TEMPORAL_PARAMS = {"start", "end", "start_time", "end_time", "limit", "page", "page_num", "cursor"}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def raw_inventory(root: str | Path) -> dict[str, Any]:
    base = Path(root).resolve()
    files = sorted(base.rglob("*.json"))
    digest = hashlib.sha256()
    paths = []
    for path in files:
        relative = path.relative_to(base).as_posix()
        paths.append(relative)
        digest.update(relative.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return {"count": len(files), "sha256": digest.hexdigest(), "paths": paths}


def _rows(payload: Any) -> list[Any]:
    value = payload
    if isinstance(value, Mapping):
        value = value.get("data", value.get("result", []))
    if isinstance(value, Mapping):
        value = value.get("data", value.get("result", value.get("rows", [])))
    return list(value) if isinstance(value, list) else ([] if value is None else [value])


def _timestamp(record: Any) -> int | None:
    value = None
    if isinstance(record, Mapping):
        for key in ("timestamp", "time", "t", "open_time", "datetime", "date"):
            if key in record:
                value = record[key]
                break
    elif isinstance(record, list) and record:
        value = record[0]
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return number // 1000 if number >= 100_000_000_000 else number
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def _event_endpoint(endpoint_id: str) -> bool:
    endpoint = endpoint_id.lower()
    return any(token in endpoint for token in ("trade", "footprint", "event", "large_limit"))


@dataclass
class AcquisitionMetrics:
    requests: int = 0
    requests_avoided: int = 0
    records_read: int = 0
    records_new: int = 0
    records_deduplicated: int = 0
    records_revised: int = 0
    late_arrivals: int = 0


class AcquisitionManager:
    """SQLite-backed acquisition state, deduplication, and watermarks."""

    def __init__(self, state_root: str | Path, *, input_raw_root: str | Path) -> None:
        self.state_root = Path(state_root)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.state_root / "acquisition.sqlite3"
        self.inventory = raw_inventory(input_raw_root)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._create_schema()
        stored = self._metadata("raw_inventory_sha256")
        count = self._metadata("raw_inventory_count")
        completed = self._metadata("bootstrap_complete") == "1"
        # RAW fixture changes after a completed bootstrap are an incremental
        # runtime event, not a reason to erase acquisition history.  The
        # continuous watcher uses this flag to force a new acquisition cycle.
        self.raw_inventory_changed = bool(
            completed
            and (stored != self.inventory["sha256"] or count != str(self.inventory["count"]))
        )
        self.cold_start = not completed
        if self.cold_start:
            self.connection.execute("DELETE FROM records")
            self.connection.execute("DELETE FROM endpoint_state")
            self._set_metadata("bootstrap_complete", "0")
        self._set_metadata("schema_version", str(SCHEMA_VERSION))
        # Commit the RAW inventory fingerprint only after a successful runtime
        # cycle (mark_complete).  If the pipeline fails, the next cycle must
        # still see the pending RAW change.
        self.connection.commit()
        self.mode = "bootstrap" if self.cold_start else "incremental"
        self.metrics: dict[str, AcquisitionMetrics] = {}
        # Exact network-request coalescing for one Main run.  This sits before
        # the provider call and safely shares identical requests between
        # families (for example CVD/Liquidity footprint windows) without
        # suppressing revisions across later runtime cycles.
        self._response_cache: dict[str, Any] = {}
        self.started = time.perf_counter()

    def _create_schema(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS endpoint_state (
                identity TEXT PRIMARY KEY, family TEXT NOT NULL, provider TEXT NOT NULL,
                endpoint_id TEXT NOT NULL, market TEXT, timeframe TEXT,
                bootstrap_complete INTEGER NOT NULL, last_seen_timestamp INTEGER,
                last_closed_timestamp INTEGER, last_processed_timestamp INTEGER,
                last_persisted_timestamp INTEGER, last_page_token TEXT,
                record_count INTEGER NOT NULL, checksum TEXT NOT NULL,
                policy TEXT NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS records (
                identity TEXT NOT NULL, record_key TEXT NOT NULL, timestamp INTEGER,
                payload TEXT NOT NULL, PRIMARY KEY(identity, record_key)
            );
            CREATE TABLE IF NOT EXISTS dirty_windows (
                id INTEGER PRIMARY KEY AUTOINCREMENT, identity TEXT NOT NULL,
                family TEXT NOT NULL, source_timeframe TEXT,
                start_timestamp INTEGER NOT NULL, end_timestamp INTEGER NOT NULL,
                cause TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                UNIQUE(identity,start_timestamp,end_timestamp,cause,status)
            );
        """)

    def _metadata(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def _set_metadata(self, key: str, value: str) -> None:
        self.connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)", (key, value))

    def wrap_router(self, router: Any) -> "AcquisitionRouter":
        return AcquisitionRouter(self, router)

    def fetch(self, family: str, fetcher: Any, request: Mapping[str, Any]) -> Any:
        provider = str(request.get("provider", ""))
        endpoint = str(request.get("endpoint_id", ""))
        params = request.get("params") if isinstance(request.get("params"), Mapping) else {}
        dimensions = request.get("dimensions") if isinstance(request.get("dimensions"), Mapping) else {}
        metric = self.metrics.setdefault(family, AcquisitionMetrics())
        network_identity = {
            "provider": provider,
            "endpoint_id": endpoint,
            "path": request.get("path"),
            "transport": request.get("transport", "rest"),
            "channel": request.get("channel"),
            "params": dict(params),
        }
        request_signature = hashlib.sha256(_json(network_identity).encode()).hexdigest()
        cached = self._response_cache.get(request_signature)
        if cached is not None:
            metric.requests_avoided += 1
            return deepcopy(cached)

        response = fetcher(**deepcopy(dict(request)))
        self._response_cache[request_signature] = deepcopy(response)
        stable_params = {k: v for k, v in params.items() if k not in TEMPORAL_PARAMS}
        identity_payload = {"family": family, "provider": provider, "endpoint_id": endpoint,
                            "params": stable_params, "dimensions": dimensions}
        identity = hashlib.sha256(_json(identity_payload).encode()).hexdigest()
        rows = _rows(response)
        metric.requests += 1
        metric.records_read += len(rows)
        event_stream = _event_endpoint(endpoint)
        timestamps = []
        old = self.connection.execute(
            "SELECT last_seen_timestamp FROM endpoint_state WHERE identity=?", (identity,),
        ).fetchone()
        previous_last_seen = old[0] if old else None
        changes: list[tuple[int, str]] = []
        for record in rows:
            timestamp = _timestamp(record)
            if timestamp is not None:
                timestamps.append(timestamp)
            fingerprint = hashlib.sha256(_json(record).encode()).hexdigest()
            record_key = fingerprint if event_stream or timestamp is None else str(timestamp)
            previous = self.connection.execute(
                "SELECT payload FROM records WHERE identity=? AND record_key=?", (identity, record_key),
            ).fetchone()
            if previous is None:
                metric.records_new += 1
                cause = "LATE_ARRIVAL" if previous_last_seen is not None and timestamp is not None and timestamp < previous_last_seen else "NEW"
                if cause == "LATE_ARRIVAL":
                    metric.late_arrivals += 1
                if timestamp is not None:
                    changes.append((timestamp, cause))
            elif previous[0] == _json(record):
                metric.records_deduplicated += 1
            else:
                metric.records_revised += 1
                if timestamp is not None:
                    changes.append((timestamp, "REVISION"))
            self.connection.execute(
                "INSERT OR REPLACE INTO records(identity,record_key,timestamp,payload) VALUES(?,?,?,?)",
                (identity, record_key, timestamp, _json(record)),
            )
        count, checksum_source = self.connection.execute(
            "SELECT COUNT(*), GROUP_CONCAT(record_key,'') FROM records WHERE identity=?", (identity,),
        ).fetchone()
        maximum = max(timestamps, default=None)
        last_seen = max([value for value in (maximum, old[0] if old else None) if value is not None], default=None)
        page_token = params.get("cursor", params.get("page", params.get("page_num")))
        policy = endpoint_policy(endpoint)
        source_timeframe = dimensions.get("timeframe", params.get("interval", params.get("window")))
        if self.mode == "incremental":
            for timestamp, cause in changes:
                self.connection.execute(
                    "INSERT OR IGNORE INTO dirty_windows(identity,family,source_timeframe,start_timestamp,end_timestamp,cause,status,created_at) VALUES(?,?,?,?,?,?,'pending',?)",
                    (identity, family, source_timeframe, timestamp, timestamp, cause, int(time.time())),
                )
        self.connection.execute("""
            INSERT OR REPLACE INTO endpoint_state(
              identity,family,provider,endpoint_id,market,timeframe,bootstrap_complete,
              last_seen_timestamp,last_closed_timestamp,last_processed_timestamp,
              last_persisted_timestamp,last_page_token,record_count,checksum,policy,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (identity, family, provider, endpoint,
                dimensions.get("market_type", params.get("market", params.get("exchange"))),
                dimensions.get("timeframe", params.get("interval", params.get("window"))),
                1, last_seen, last_seen, last_seen, last_seen,
                None if page_token is None else str(page_token), int(count),
                hashlib.sha256(str(checksum_source or "").encode()).hexdigest(),
                policy.resampling_class.value, int(time.time())))
        self.connection.commit()
        return response

    def summary(self) -> dict[str, Any]:
        endpoints, persisted = self.connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(record_count),0) FROM endpoint_state"
        ).fetchone()
        return {
            "mode": self.mode,
            "cold_start": self.cold_start,
            "database": str(self.database_path),
            "raw_inventory": {"count": self.inventory["count"], "sha256": self.inventory["sha256"]},
            "raw_inventory_changed": self.raw_inventory_changed,
            "endpoint_states": int(endpoints),
            "records_persisted": int(persisted),
            "duration_seconds": round(time.perf_counter() - self.started, 6),
            "families": {family: vars(metric) for family, metric in self.metrics.items()},
        }

    def overlay_changed(self, version: int) -> bool:
        stored = self._metadata("overlay_version")
        if stored is None and int(version) == 0:
            self.mark_overlay_processed(0)
            return False
        return stored != str(int(version))

    def mark_overlay_processed(self, version: int) -> None:
        self._set_metadata("overlay_version", str(int(version)))
        self.connection.commit()

    def pending_dirty_windows(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT id,family,source_timeframe,start_timestamp,end_timestamp,cause FROM dirty_windows WHERE status='pending' ORDER BY id"
        ).fetchall()
        return [{"id": row[0], "family": row[1], "source_timeframe": row[2],
                 "start_timestamp": row[3], "end_timestamp": row[4], "cause": row[5]} for row in rows]

    def mark_dirty_clean(self, ids: list[int]) -> None:
        if ids:
            self.connection.executemany("DELETE FROM dirty_windows WHERE id=? AND status='pending'", [(int(value),) for value in ids])
            self.connection.commit()

    def mark_complete(self) -> None:
        self._set_metadata("bootstrap_complete", "1")
        self._set_metadata("raw_inventory_sha256", self.inventory["sha256"])
        self._set_metadata("raw_inventory_count", str(self.inventory["count"]))
        self.raw_inventory_changed = False
        self.connection.commit()

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()


@dataclass(frozen=True)
class _FamilyFetcher:
    manager: AcquisitionManager
    family: str
    fetcher: Any

    def __call__(self, **request: Any) -> Any:
        return self.manager.fetch(self.family, self.fetcher, request)


@dataclass(frozen=True)
class AcquisitionRouter:
    manager: AcquisitionManager
    router: Any

    def for_family(self, family: str) -> _FamilyFetcher:
        return _FamilyFetcher(self.manager, family, self.router.for_family(family))
