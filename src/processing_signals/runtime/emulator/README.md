# Runtime provider emulator

This package emulates the external provider boundary used by Input. It does **not**
construct TradELATIN Input contracts and it does not calculate market features.

The canonical fixture bodies remain under:

```text
runtime/contracts/input_raw/
├── coinglass/synthetic_raw/<family>/...
├── cryptoquant/synthetic_raw/<family>/...
└── glassnode/synthetic_raw/<family>/...
```

Use one router for all eight families:

```python
from processing_signals.runtime.emulator import SyntheticProviderRouter

router = SyntheticProviderRouter("runtime/contracts/input_raw")
fetcher = router.for_family("prices_ohlcv")
```

The returned callable accepts the same `provider`, `endpoint_id`, `path`, and
`params` request envelope expected by each Input raw extractor. Provider adapters
apply request filtering, limits, time windows, pagination semantics, and snapshot
selection while preserving provider-shaped response bodies.

Supported upstream providers are CoinGlass, CryptoQuant, and Glassnode only.
