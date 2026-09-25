# Equities Observable Catalog

## Catalog status

**Status: PROPOSED EQUITIES OBSERVABLE / NOT IMPLEMENTED**

This catalog assigns documentation-only IDs to the Equities observables found
necessary from the SSL Market/IBKR source analysis. The IDs are not route IDs,
endpoint IDs, registry entries or C9 observables.

The existing BTC/CRYPTO inventory remains frozen at **33 logical endpoints
across C1–C8**: 20 CoinGlass, 4 CryptoQuant and 9 Glassnode. Current Equities
endpoint count: **0**. Current C9 endpoint count: **0**.

## Source evidence

The SSL Market package consumes or constructs equivalents of IBKR Level I
market data, daily historical OHLC, 5-second `TRADES` bars, Tick-by-Tick `Last`,
SMART/direct market depth, reference instruments, shortable shares and an
indicative halt field. SSL does not establish source support for Tick-by-Tick
BidAsk, an options chain, option open interest, put/call metrics, market Greeks,
an equity implied-volatility surface, borrow fee, participant identity,
institutional flows or liquidations.

Availability values below describe the approved source model, not current
Emulator runtime availability. Every row remains **PROPOSED / NOT
IMPLEMENTED**.

## C1 — Prices

| ID | Proposed observable | Class | SSL/IBKR source | Purpose | Availability and limitation |
|---|---|---|---|---|---|
| `EQ.C1.1` | Daily OHLCV | RAW | Historical bars | Reproduce session price/volume bars. | `COMPLETE`; preserve session, timezone, bar size and adjustment policy. |
| `EQ.C1.2` | 5-second trade bar | RAW | Real-time `TRADES` bars | Reproduce intraday OHLC, volume, WAP and trade count. | `COMPLETE`; not a quote bar. |
| `EQ.C1.3` | Last trade | RAW | Tick-by-Tick `Last` | Reproduce timestamped trade price and size. | `COMPLETE`; conditions/exchange fields remain source dependent. |
| `EQ.C1.4` | Level I quote | RAW | Market data bid/ask ticks | Reproduce best bid/ask and visible sizes. | `COMPLETE`; not full depth. |
| `EQ.C1.5` | Traded volume | RAW | Market data and trade bars | Reproduce observable volume with declared interval. | `COMPLETE`; source volume conventions must remain explicit. |

## C2 — CVD & Order Flow

| ID | Proposed observable | Class | SSL/IBKR source | Purpose | Availability and limitation |
|---|---|---|---|---|---|
| `EQ.C2.1` | Time & Sales trade | RAW | Tick-by-Tick `Last` | Reproduce ordered trade prints. | `PARTIAL`; consolidated/venue coverage depends on subscription and routing. |
| `EQ.C2.2` | Estimated aggressor side | INFERRED | Trade plus contemporaneous quote | Classify `BUY`, `SELL` or `UNKNOWN`. | `PARTIAL`; method/version and quote timing are mandatory; no actor identity. |
| `EQ.C2.3` | Classified buy/sell/unknown volume | DERIVED | `EQ.C2.1–2` | Preserve signed and unclassified volume totals. | `PARTIAL`; unknown volume must remain visible. |
| `EQ.C2.4` | Cumulative volume delta | DERIVED | `EQ.C2.3` | Validate deterministic CVD over a declared range. | `PARTIAL`; report classification coverage; trades do not imply CVD automatically. |
| `EQ.C2.5` | Large trade event | DERIVED | Tick-by-Tick `Last` | Mark prints meeting a versioned size/notional rule. | `PARTIAL`; large does not mean institutional and is not predictive. |

## C3 — Open Interest / Positioning

| ID | Proposed observable | Class | SSL/IBKR source | Purpose | Availability and limitation |
|---|---|---|---|---|---|
| `EQ.C3.1` | Shortable shares | RAW | Generic market-data tick 236 | Reproduce reported shortable-share availability. | `PARTIAL`; broker-specific availability, not short interest, option OI or borrow fee. |

## C6 — Volatility

| ID | Proposed observable | Class | SSL/IBKR source | Purpose | Availability and limitation |
|---|---|---|---|---|---|
| `EQ.C6.1` | Realized volatility | DERIVED | Price/trade-bar history | Validate observed return dispersion over a declared window. | `PARTIAL`; formula, sampling, annualization and minimum coverage are mandatory. |
| `EQ.C6.2` | VIX reference price | RAW | VIX reference instrument quote | Reproduce an observable volatility-index value. | `PARTIAL`; a VIX quote is not the selected asset's implied-volatility surface. |
| `EQ.C6.3` | Reference-market price | RAW | SPY, QQQ, SMH, ES or NQ quote | Reproduce explicit cross-market reference context. | `PARTIAL`; context only, not a regime or prediction. |

## C8 — Liquidity Microstructure

| ID | Proposed observable | Class | SSL/IBKR source | Purpose | Availability and limitation |
|---|---|---|---|---|---|
| `EQ.C8.1` | Level I bid/ask state | RAW | Market data bid/ask ticks | Reproduce best prices and visible sizes. | `COMPLETE`; retain venue/routing context. |
| `EQ.C8.2` | Market-depth row | RAW | SMART/direct `reqMktDepth` | Reproduce price, size, side, position and operation. | `COMPLETE`; visible subscribed depth only. |
| `EQ.C8.3` | Order-book update stream | RAW | Ordered depth callbacks | Reconstruct deterministic book state. | `COMPLETE`; sequence and reset boundaries are required. |
| `EQ.C8.4` | Quoted spread | DERIVED | `EQ.C8.1` | Validate `ask - bid` and optional basis-point form. | `COMPLETE`; crossed/locked/invalid quotes require explicit quality handling. |
| `EQ.C8.5` | Visible depth imbalance | DERIVED | `EQ.C8.2–3` | Compare declared bid/ask depth ranges. | `COMPLETE`; not a directional signal. |
| `EQ.C8.6` | Depth concentration | DERIVED | `EQ.C8.2–3` | Measure visible liquidity concentration by level/range. | `COMPLETE`; does not include hidden liquidity. |
| `EQ.C8.7` | Large visible order | DERIVED | `EQ.C8.2–3` | Identify rows meeting a versioned visible-size rule. | `COMPLETE`; visibility is not execution or participant identity. |
| `EQ.C8.8` | Liquidity persistence | DERIVED | Repeated depth states | Measure how long qualifying visible liquidity remains observed. | `COMPLETE`; does not prove intent. |
| `EQ.C8.9` | Liquidity disappearance | DERIVED | Depth remove/change events | Record loss of previously visible liquidity. | `COMPLETE`; cannot distinguish cancellation, fill or feed change without evidence. |
| `EQ.C8.10` | Liquidity replenishment | DERIVED | Ordered depth events | Record visible size restored after removal or consumption. | `COMPLETE`; no participant attribution. |
| `EQ.C8.11` | Sweep-like sequence | DERIVED | Trades plus ordered depth changes | Reproduce a multi-level observable consumption pattern. | `COMPLETE`; “sweep-like” is descriptive, not actor identity or intent. |
| `EQ.C8.12` | Inferred large-liquidity activity | INFERRED | Large-order, persistence and depth-event evidence | Validate a bounded inference from observable inputs. | `PARTIAL`; evidence/rule/confidence required; no spoofing or institutional claim. |

## Required fixture and scenario map

| SSL observable | C-family | Raw/Derived/Inferred | Required future fixture | Suggested deterministic scenarios |
|---|---|---|---|---|
| Historical and 5-second OHLCV | C1 | RAW | Timestamped bars with explicit session/units | normal, quiet, high-volume, volatility expansion/contraction |
| Last trades / Time & Sales | C1, C2, C8 | RAW | Ordered trade events plus source metadata | aggressive buying/selling, large buy/sell, burst of large trades, gap |
| Bid/ask and sizes | C1, C8 | RAW | Level I quote-event stream | spread widening/compression, stale data, locked/crossed quality case |
| Depth rows and updates | C8 | RAW | Initial book plus ordered insert/update/delete events | thin/deep, bid-heavy, ask-heavy, balanced, removal/replenishment |
| Estimated aggressor flow and CVD | C2 | INFERRED / DERIVED | Trades synchronized with quote evidence and expected classification | buying, selling, mixed flow and explicit UNKNOWN coverage |
| Large-trade classification | C2, C8 | DERIVED | Trades around a versioned threshold | large buy, large sell, threshold boundary, burst |
| Spread, imbalance and concentration | C8 | DERIVED | Level I/depth inputs plus expected calculations | widening/compression and thin/deep/balanced/heavy books |
| Large visible orders and persistence | C8 | DERIVED | Repeated depth states with controlled changes | large bid/ask, persistence, disappearance, cancellation-like removal |
| Sweep-like sequence | C8 | DERIVED | Synchronized prints and multi-level depth depletion | observable buy-side and sell-side sweep-like sequences |
| Realized volatility | C6 | DERIVED | Price series plus formula/window/annualization | expansion and contraction |
| VIX/reference instruments | C6 | RAW | Synchronized reference quotes | normal, stale reference and temporary gap |
| Shortable shares | C3 | RAW | Timestamped broker-reported quantity/status | available, constrained, stale and unavailable |

## Expected-value requirements

Every future fixture must contain known expected values or explicit tolerances.
At minimum:

- bars: exact OHLC, volume, WAP/trade-count fields where supplied;
- trades: event count, total volume, large-trade IDs and threshold version;
- C2: BUY/SELL/UNKNOWN volume, classification coverage and terminal CVD;
- Level I: bid, ask, sizes, spread and data age;
- depth: exact reconstructed rows, bid/ask totals, imbalance, concentration and
  event-sequence checksum;
- liquidity events: qualifying order IDs/levels, first/last observation,
  duration, disappearance/replenishment evidence and inference confidence;
- volatility: return convention, window, annualization and expected result;
- quality cases: expected `source_status`, gap interval and stale-age boundary.

## Blocked observables

The following must not be simulated until a validated source and semantic
contract are approved: option open interest, put/call metrics, option-chain
implied volatility, market Greeks, dealer/GEX positioning, real borrow fee,
institutional/fund flows, dark-pool or participant identity, hidden liquidity,
queue position, spoofing intent, full consolidated order book, forced
liquidations, and blockchain observables.

No proxy from C1, C2, C6 or C8 may be relabeled as one of these blocked
observables.
