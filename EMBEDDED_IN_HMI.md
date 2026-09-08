# Embedded Emulator runtime

This directory is an independent FastAPI/Uvicorn service embedded in the Screen repository.

Normal startup is now from the Screen root:

```bash
python main.py
```

`Screens/main.py` checks `http://127.0.0.1:8000/health`, validates the exact 33-endpoint inventory, starts this service only when needed, then starts Dash. If the HMI created the Emulator process, it also terminates it when the HMI exits. If an already-running compatible Emulator is detected, HMI reuses it and leaves it running on exit.

Processing remains a separate repository/process and connects through Acquisition.
