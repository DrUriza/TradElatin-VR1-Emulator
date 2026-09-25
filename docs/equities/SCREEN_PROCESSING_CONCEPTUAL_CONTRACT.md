# Equities Screen–Processing Conceptual Contract

## Status

**Status: DOCUMENTATION-ONLY / PROPOSED / NOT OPERATIONAL**

This document defines the information boundary Screen expects from Processing
for future multi-market presentation. It is deliberately not an operational
JSON schema, API, route, provider contract or fixture format.

Screen never connects directly to IBKR. A future acquisition/normalization
path supplies Processing; Processing supplies a versioned VR1 observable
contract to Screen.

```text
Real source or VR1 Emulator replay/synthetic source
→ acquisition and normalization
→ Processing contract
→ Screen
```

## Required context

Every Screen payload conceptually carries:

| Field | Required meaning |
|---|---|
| `market_class` | Canonical market domain, initially `CRYPTO` or proposed `EQUITIES`. It selects semantics; it does not rewrite IDs. |
| `asset_id` | Stable normalized instrument identity, distinct from a display ticker and sufficient to disambiguate venue/security type where required. |
| `family` | One of `C1` through `C9`, preserving canonical family ownership. |
| `source_status` | Current acquisition/replay state, separate from family capability and data quality. |
| `data_quality` | Timestamped assessment of freshness, completeness, ordering and validity for the supplied observation window. |
| `capabilities` | Explicit supported, partial, unsupported and not-applicable observable capabilities for the selected market/asset/family. |

An observable also requires its proposed observable ID, transformation class
(`RAW`, `DERIVED`, or `INFERRED`), value, units, event/observation time,
provenance and contract/method version. Derived and inferred values require
references to their inputs and method parameters.

## Conceptual invariants

1. `market_class`, `asset_id` and `family` define the navigation context; an
   observable must not migrate across it implicitly.
2. `capabilities` describes what the contract can represent. `source_status`
   describes whether the source is currently usable. `data_quality` describes
   the supplied data. These concerns must not be collapsed into one flag.
3. A missing or stale value is not numeric zero.
4. `NOT_APPLICABLE` is not `UNSUPPORTED`; the former rejects the domain mapping,
   while the latter records a meaningful capability lacking an approved source
   model.
5. Equities proposed observable IDs are independent from the 33 registered
   BTC/CRYPTO endpoints and from C9 proposed Stacks/sBTC source surfaces.
6. Screen renders the supplied observable and limitations; it does not derive
   higher-level market structure or trading action.

## Source status vocabulary

The operational enum remains to be versioned, but the concept must distinguish
at least:

- live/current source data;
- deterministic synthetic data;
- deterministic replay data;
- stale data;
- temporary gap or incomplete interval;
- unavailable source; and
- unsupported or not-applicable capability.

Synthetic and replay status must remain visible to Screen. Neither may
masquerade as a live provider response.

## Data-quality dimensions

`data_quality` should be able to report, without implying a trading judgment:

- observation and receipt timestamps plus computed age;
- expected and observed interval coverage;
- gaps, duplicates and out-of-order events;
- quote validity such as normal, locked, crossed or incomplete;
- classification coverage including explicit UNKNOWN trade volume;
- depth coverage, routing/venue context and number of visible levels;
- unit, currency, timezone and session metadata; and
- provenance and transformation/method version.

## Capability model

Capabilities are evaluated for the selected `market_class`, `asset_id` and
`family`. Each capability carries one of `COMPLETE`, `PARTIAL`, `UNSUPPORTED`
or `NOT_APPLICABLE`, plus a human-readable limitation and the set of proposed
observable IDs it covers.

For the initial Equities design:

- C1 and the frozen SSL-derived C8 scope are `COMPLETE`;
- C2, C3 and C6 are `PARTIAL`;
- C4 and C7 are `UNSUPPORTED`; and
- C5 and C9 are `NOT_APPLICABLE`.

“Complete” is bounded by the approved catalog scope and never claims total
coverage of all possible Equities data.

## C2 and C8 contract constraints

For C2, the contract must preserve BUY/SELL/UNKNOWN classifications, unknown
volume, classification coverage, quote evidence and method version. Estimated
aggressor side is inferred and cannot identify a participant.

For C8, the contract must preserve ordered depth events, book reset/snapshot
boundaries, visible levels, venue/routing context and derivation ranges. Large
visible orders, persistence, disappearance, replenishment and sweep-like
sequences describe observable evidence only; they do not establish identity,
intent, spoofing, hidden liquidity, or direction.

## Deterministic Emulator compatibility

The future Processing contract must be satisfiable by either a normalized real
source or VR1 Emulator synthetic/replay data. For Emulator scenarios it must
retain scenario ID, fixture version, deterministic seed, controlled clock,
input-event provenance and expected-value/method version. Replaying the same
fixture version and seed must produce the same ordered inputs and expected VR1
observables.

## Explicit exclusions

This contract does not include continuation, exhaustion, structural
absorption, impulse, breakout, regime, setup, LONG/SHORT, predictive score,
READY/NO_TRADE, entry, stop, targets, sizing, risk, orders, fills or execution.
Those belong to VR2–VR4 and must not be inferred by Screen from VR1 fields.

No Equities route, runtime schema or HMI component is authorized by this
document. Those require separate approval after this design is frozen.
