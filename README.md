# TradELATIN VR1 Emulator V4.2

Standalone FastAPI market-data Emulator.

- Internal market state advances every **1 second**.
- Endpoints publish only when they receive an HTTP request.
- Frozen inventory: **33 logical endpoints**.
- Emulator historical transport policy: **500 records per request**.
- Prices uses a moderately more abrupt stochastic profile in build HF2:
  regime volatility, order-flow impact and jump amplitude are higher, while
  regime durations, jump frequency and the ±12% per-step safety clamp remain
  unchanged.

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
