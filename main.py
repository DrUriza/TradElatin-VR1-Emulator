"""TradELATIN runtime entrypoint.

Default operation is selective/continuous:

    python main.py

The initial run guarantees the eight published contracts exist. After that:
- Prices acquisition: every 5 s
- CVD acquisition: every 30 s
- Open Interest acquisition: every 60 s
- ETF, On-Chain, Volatility, Liquidations and Liquidity: manual/on-demand only

Each acquisition poll reaches Processing only when its source data changed, or
when a dependency change marked that family dirty. Manual refresh requests are
read from ``runtime/control/requests``.

Use ``python main.py --once`` for one complete eight-family run and exit.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DATA_SOURCE = "emulator"

REPO_ROOT = Path(__file__).resolve().parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

CONTRACTS_ROOT = REPO_ROOT / "runtime/contracts"
INPUT_RAW_ROOT = CONTRACTS_ROOT / "input_raw"
CONTROL_ROOT = REPO_ROOT / "runtime/control"


def _load_local_env(path: Path = REPO_ROOT / ".env") -> None:
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


def _run_once() -> None:
    from processing_signals.main.runtime_orchestrator import run_and_export_all

    _, publication = run_and_export_all(
        source=DATA_SOURCE,
        input_raw_root=INPUT_RAW_ROOT,
        contracts_root=CONTRACTS_ROOT,
    )
    print(f"TradELATIN Main completed | source={DATA_SOURCE} | families=8", flush=True)
    print(json.dumps(publication["quality"], ensure_ascii=False, indent=2), flush=True)
    print(f"Contracts: {publication['root']}", flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TradELATIN selective runtime")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run all eight families once and exit.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.environ.get("TRADELATIN_SCHEDULER_POLL_SECONDS", "0.5")),
        help="Scheduler/control-folder scan interval (default: 0.5s).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _load_local_env()
    args = _parser().parse_args(argv)
    if args.poll_seconds <= 0:
        raise SystemExit("poll-seconds must be > 0")

    if args.once:
        _run_once()
        return 0

    from processing_signals.runtime.selective_scheduler import SelectiveRuntimeScheduler

    scheduler = SelectiveRuntimeScheduler(
        source=DATA_SOURCE,
        contracts_root=CONTRACTS_ROOT,
        input_raw_root=INPUT_RAW_ROOT,
        control_root=CONTROL_ROOT,
        poll_seconds=args.poll_seconds,
    )
    scheduler.bootstrap()
    try:
        scheduler.run_forever()
    except KeyboardInterrupt:
        print("\nTradELATIN selective runtime stopped by user.", flush=True)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
