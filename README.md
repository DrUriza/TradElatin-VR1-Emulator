# TradELATIN VR1 Emulator V4.2

Standalone FastAPI market-data Emulator.

- Internal market state advances every **1 second**.
- Endpoints publish only when they receive an HTTP request.
- Frozen inventory: **33 logical endpoints**.
- The frozen 33-endpoint inventory refers to the existing C1–C8 architecture. Stacks/C9 is proposed grant work and is not included in the current Emulator endpoint inventory.
- Emulator historical transport policy: **500 records per request**.
- Prices uses a moderately more abrupt stochastic profile in build HF2:
  regime volatility, order-flow impact and jump amplitude are higher, while
  regime durations, jump frequency and the ±12% per-step safety clamp remain
  unchanged.

## C9 / Stacks proposed extension

The current Emulator exposes **no C9 endpoints** and contains **no Stacks
provider, routes, or fixtures**. Synthetic and replay-compatible Stacks/sBTC
responses would be implemented only as part of the proposed funded work. This
documentation does not implement Milestone 1 and does not claim that C9 is
operational.

The C9 pre-implementation catalog freezes 14 proposed logical source surfaces — 6 Core and 8 transversal — but none is part of the current 33-endpoint Emulator registry.

- [C9 Stacks Technical Specification](docs/c9/stacks/C9_STACKS_TECHNICAL_SPEC.md)
- [C9 Stacks Endpoint Catalog](docs/c9/stacks/C9_STACKS_ENDPOINT_CATALOG.md)

## Run

On Windows, `start.bat` or `./start.ps1` creates the local virtual environment
and starts the service. Manual setup is also supported:

```powershell
python -m pip install -r requirements.txt
python main.py
```

Default URL: `http://127.0.0.1:8000`.

Environment variables:

- `TRADELATIN_EMULATOR_HOST`
- `TRADELATIN_EMULATOR_PORT`

### Liquidations response latency

Liquidation and Long/Short positioning history use four deterministic stochastic samples per output candle to reduce manual-refresh latency while retaining the frozen 500-record response contract. Prices, CVD and OI keep their original richer historical sampling; this optimization is scoped to Liquidations/positioning only.
