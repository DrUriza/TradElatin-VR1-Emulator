from __future__ import annotations

from pathlib import Path
from typing import Any

from processing_signals.runtime.emulator import SyntheticProviderRouter
from processing_signals.runtime.providers import RealProviderRouter

VALID_SOURCES = {"emulator", "real"}


def build_provider_router(source: str, *, input_raw_root: str | Path | None = None,
                          overlay_root: str | Path | None = None, **kwargs: Any):
    normalized = str(source).strip().lower()
    if normalized == "emulator":
        return SyntheticProviderRouter(input_raw_root, overlay_root=overlay_root)
    if normalized == "real":
        return RealProviderRouter(**kwargs)
    raise ValueError(f"Unsupported DATA_SOURCE={source!r}. Expected 'emulator' or 'real'.")
