from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


class FixtureStore:
    """Read-only access to provider-shaped synthetic raw responses."""

    def __init__(self, input_raw_root: str | Path) -> None:
        self.root = Path(input_raw_root).resolve()
        if not self.root.exists():
            raise FileNotFoundError(self.root)

    def family_root(self, provider: str, family: str) -> Path:
        return self.root / provider / "synthetic_raw" / family

    def load(self, provider: str, family: str, *parts: str) -> Any:
        path = self.family_root(provider, family).joinpath(*parts)
        if not path.exists():
            raise FileNotFoundError(path)
        return deepcopy(json.loads(path.read_text(encoding="utf-8")))

    def glob(self, provider: str, family: str, pattern: str) -> list[Path]:
        return sorted(self.family_root(provider, family).glob(pattern))


def default_input_raw_root() -> Path:
    """Find ``runtime/contracts/input_raw`` from a source checkout."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "runtime" / "contracts" / "input_raw"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("runtime/contracts/input_raw not found from emulator package")
