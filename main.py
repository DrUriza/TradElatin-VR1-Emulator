"""TradELATIN single runtime entrypoint.

Run only this file:

    python main.py

Change DATA_SOURCE to switch the acquisition backend without changing Input,
Processing, Classification or Contract Builder.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ============================================================
# ONLY LINE YOU CHANGE TO SWITCH DATA SOURCE
#   "emulator" -> provider-shaped RAW fixtures
#   "real"     -> CoinGlass / CryptoQuant / Glassnode APIs
# ============================================================
DATA_SOURCE = "emulator"

REPO_ROOT = Path(__file__).resolve().parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

CONTRACTS_ROOT = REPO_ROOT / "runtime/contracts"

def _load_local_env(path: Path = REPO_ROOT / ".env") -> None:
    """Load simple KEY=VALUE pairs without adding another runtime dependency."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    _load_local_env()
    from processing_signals.main.runtime_orchestrator import run_and_export_all

    _, publication = run_and_export_all(
        source=DATA_SOURCE,
        contracts_root=CONTRACTS_ROOT,
    )
    print(f"TradELATIN Main completed | source={DATA_SOURCE} | families=8")
    print(json.dumps(publication["quality"], ensure_ascii=False, indent=2))
    print(f"Contracts: {publication['root']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
