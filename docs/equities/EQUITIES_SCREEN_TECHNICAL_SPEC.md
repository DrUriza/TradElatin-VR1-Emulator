# Equities Screen Technical Specification

## Status and scope

**Status: PROPOSED EQUITIES OBSERVABLE / DOCUMENTATION ONLY**

This specification freezes the pre-implementation design for presenting
Equities observables to Screen. It is limited to VR1 market observability. It
does not implement HMI views, Processing contracts, HTTP routes, providers,
fixtures, tests, signals, execution, or any other runtime behavior.

The operational BTC/CRYPTO inventory remains frozen at **33 logical endpoints
across C1–C8**: 20 CoinGlass, 4 CryptoQuant and 9 Glassnode. Equities
observables are a separate proposed inventory and are not registered
endpoints. C9 remains independent, PRE-IMPLEMENTATION / PROPOSED / NOT
IMPLEMENTED, and is not an Equities family.

## VR1 responsibility

VR1 answers: **Can the data and observable that Screen would receive from a
real source be reproduced deterministically?**

VR1 may expose raw, derived and inferred observables with provenance. It must
not decide market structure, future direction, trade suitability, risk, or
execution.

## Multi-market navigation

Screen navigation is frozen conceptually as:

```text
Market → Asset → C-family → Observable
```

- `Market` selects an isolated market profile such as `CRYPTO` or `EQUITIES`.
- `Asset` selects a canonical instrument identifier within that profile.
- `C-family` selects the analytical family without changing its ownership.
- `Observable` displays VR1 facts and quality/provenance metadata only.

Screen must not connect directly to IBKR or any other market-data source. It
consumes a versioned contract supplied through Processing. Selecting
`EQUITIES` must not reinterpret, rename, or mutate existing BTC endpoints or
fixtures.

## Screen availability states

| State | Screen meaning |
|---|---|
| `COMPLETE` | All observables in the approved Equities scope for that family are reproducible and meet their declared source and quality contract. It does not claim universal coverage of the family. |
| `PARTIAL` | A useful subset is available, while named source fields or semantics remain unavailable or limited. |
| `UNSUPPORTED` | The family is meaningful for Equities, but SSL/IBKR evidence does not provide an adequate source model. Screen must show the reason and must not fabricate substitute metrics. |
| `NOT APPLICABLE` | The family does not apply to the selected Equities source/domain. Screen must not render a synthetic zero or imply missing data. |

`source_status` and `data_quality` are independent of family availability. A
normally `COMPLETE` family may still report a stale or gapped source in a
specific scenario.

## Frozen Equities family map

| Family | Screen state | Approved Equities scope | Explicit boundary |
|---|---|---|---|
| C1 — Prices | `COMPLETE` | OHLCV, 5-second trade bars, last trade, Level I quote and traded volume | Complete only for the frozen SSL-derived price scope. |
| C2 — CVD & Order Flow | `PARTIAL` | Time & Sales, BUY/SELL/UNKNOWN classification, signed volume, CVD and large-trade classification | Aggressor side is estimated unless explicitly source-provided. |
| C3 — Open Interest / Positioning | `PARTIAL` | Shortable-share availability only | No options OI, put/call, Greeks, borrow fee, futures OI or funding. |
| C4 — Flows | `UNSUPPORTED` | None | No validated institutional, fund or venue-flow source model. |
| C5 — On-Chain / Market State | `NOT APPLICABLE` | None | Equity market data is not on-chain evidence. |
| C6 — Volatility | `PARTIAL` | Realized volatility and observable VIX/reference-market prices | No implied volatility surface or structural volatility regime. |
| C7 — Liquidations / Stress | `UNSUPPORTED` | None | No forced-liquidation source; volatility and liquidity events must not be relabeled as liquidations. |
| C8 — Liquidity Microstructure | `COMPLETE` | Level I/II, depth updates, spread, imbalance, Time & Sales, large trades/orders and liquidity-event sequences | Complete only for the frozen observable SSL-derived depth/tape scope; no participant identity or hidden liquidity. |
| C9 — Blockchain Financial Networks | `NOT APPLICABLE` | None | C9 remains the independent proposed Stacks/sBTC workstream. |

## C8-first Screen design

C8 is the first Equities presentation target because SSL provides the strongest
source evidence for it. Screen should be able to display:

- Level I best bid, best ask and their visible sizes;
- Level II bid/ask rows and ordered depth changes;
- quoted spread, visible depth totals, imbalance and concentration;
- Time & Sales alongside estimated aggressor flow;
- large trades and large visible orders;
- persistence, disappearance, withdrawal and replenishment of visible
  liquidity;
- observable sweep-like sequences; and
- inferred large-liquidity activity with its evidence and confidence limits.

These are market observables. They are not claims about participant identity,
intent, spoofing, absorption, continuation, exhaustion, breakout, or future
direction.

## Semantic guardrails

### C2

- Every classified trade must retain `BUY`, `SELL`, or `UNKNOWN`; unknown
  volume must not be silently allocated.
- Estimated aggressor side is an inference from an explicit classification
  rule, not a source-reported participant identity.
- CVD is derived from classified trades and must expose coverage, unknown
  volume, input range and method version.
- Time & Sales, traded volume and large-trade classification are not
  automatically CVD.
- A size threshold creates a large-trade observable, not a prediction or a
  statement about institutional participation.

### C8

- Visible depth is not total or hidden liquidity.
- Market depth is venue/routing-context dependent and must retain that context.
- Imbalance is a derived ratio over a declared depth range; it is not a
  directional signal.
- Persistence and disappearance describe observed order-book events only;
  neither proves intent, execution, cancellation motive, or spoofing.
- A sweep-like sequence describes observable prints/depth changes and must not
  assert actor identity.
- Large visible orders and large-liquidity activity must remain distinct from
  filled large trades.

## Raw, derived and inferred presentation

Screen must label the transformation class and retain traceability:

| Class | Examples | Required presentation |
|---|---|---|
| `RAW` | trade price/size, bid, ask, depth row, shortable shares | Source, timestamp, units and source status. |
| `DERIVED` | spread, CVD, realized volatility, imbalance, large-trade classification | Input references, method/version, parameters and expected value/tolerance. |
| `INFERRED` | estimated aggressor side, inferred large-liquidity activity | Evidence, rule/version, uncertainty and explicit non-identity disclaimer. |

## Scenario behavior

The Screen design must accommodate deterministic scenarios for normal and
quiet markets, high volume, spread widening/compression, thin/deep and
bid-heavy/ask-heavy/balanced books, aggressive buying/selling, large trades,
large visible orders, liquidity persistence/disappearance/removal/
replenishment, sweep-like sequences, volatility expansion/contraction, stale
data and temporary gaps.

For each scenario the future fixture contract must define deterministic seed,
controlled timestamps, explicit units, controlled noise, fixture version,
input events, expected observable values and tolerances. The same scenario,
fixture version and seed must reproduce the same expected result.

## Separation from VR2–VR4

The following are outside this specification and must not appear as VR1 Screen
outputs:

- **VR2:** continuation, exhaustion, absorption as structural state, impulse,
  breakout or structural regime;
- **VR3:** setup detection, LONG/SHORT, predictive scores, READY or NO_TRADE;
- **VR4:** entry, stop, targets, position sizing, risk, paper trading, orders,
  fills or execution.

Screen may display source and data-quality state. It must never promote those
states into a trading state or recommendation.

## Pre-implementation boundary

This document freezes presentation semantics only. There are currently no
Equities routes, registered endpoints, providers, fixtures or operational JSON
contracts in the Emulator. HMI work starts only after the observable catalog
and the conceptual Screen–Processing contract are approved.
