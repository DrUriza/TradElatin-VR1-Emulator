# C9 / Stacks Technical Specification

## Status and scope

**Status: PROPOSED / NOT IMPLEMENTED**

This document defines a future C9/Stacks extension for the TradELATIN VR1
Emulator. It is grant-scope pre-implementation documentation only. It does not
add endpoints, providers, routes, fixtures, public probes, or Stacks API calls,
and Milestone 1 has not started.

**Frozen inventory: 33 logical endpoints**

The frozen 33-endpoint inventory refers exclusively to the existing C1–C8
architecture: 20 CoinGlass, 4 CryptoQuant and 9 Glassnode. The current C9/Stacks
endpoint count is zero.

The C9 pre-implementation catalog freezes 14 proposed logical source surfaces — 6 Core and 8 transversal — but none is part of the current 33-endpoint Emulator registry.

## Architectural boundary

The proposed end-to-end architecture is:

```text
Stacks/sBTC
→ Adapter
→ Normalization
→ Versioned C9 Contracts
→ Financial Observables
→ HMI
```

C9 is the ownership boundary for Stacks/sBTC acquisition, normalization and
versioned observability. The HMI consumes financial observables rather than
raw provider payloads.

"C9 owns the Stacks/sBTC data, while C1–C8 can consume C9 observables for cross-family contextual analysis."

Cross-family consumption does not transfer semantic ownership to C1–C8 and
must not silently reinterpret on-chain activity as exchange or derivatives
market data.

## Future Emulator role

When funded and implemented, the Emulator would reproduce provider-compatible
response shapes and deterministic replay/synthetic behavior behind the same
contract boundary used by real acquisition:

```text
Real Stacks/Hiro/Emily provider shape
↕ compatible contract
VR1 Emulator synthetic/replay responses
↓
Processing acquisition
↓
Normalization
↓
C9 contracts
```

This future role would permit Processing and Integration tests without
requiring live provider availability. No Stacks provider, Stacks HTTP route,
Stacks fixture, synthetic response, or replay response exists in the current
repository.

## Frozen C9 financial observables

The proposed C9 scope freezes exactly three financial observables:

1. **C9.1 — sBTC Supply & Peg State** — observable supply, mint/burn
   reconciliation and peg-state context derived from normalized Stacks/sBTC
   evidence.
2. **C9.2 — sBTC Bridge Flow** — normalized deposit and withdrawal activity
   across the sBTC bridge, preserving direction, amount, status and time.
3. **C9.3 — sBTC Bridge Operational State** — bridge limits, chain state and
   publicly observable signer/registry state needed to describe
   protocol-defined operational constraints.

Their runtime contracts, endpoint paths, adapters, fixtures and replay engines
remain **PROPOSED / NOT IMPLEMENTED**.

## Canonical proposed logical source-surface map

All 14 IDs below are proposed logical source surfaces. They are not registered
endpoints.

### Core surfaces

| Proposed logical source surface | C9 observable ownership/support | Contextual consumers C1–C8 | Status |
|---|---|---|---|
| `sbtc_token_supply` | C9.1 | C5 | PROPOSED / NOT IMPLEMENTED |
| `sbtc_bridge_deposits` | C9.2; may contribute to C9.3 where operational fields apply | C4, C5, C6 | PROPOSED / NOT IMPLEMENTED |
| `sbtc_bridge_withdrawals` | C9.2; may contribute to C9.3 where operational fields apply | C4, C5, C6 | PROPOSED / NOT IMPLEMENTED |
| `sbtc_bridge_limits` | C9.3 | C4, C6 | PROPOSED / NOT IMPLEMENTED |
| `sbtc_bridge_chainstate` | C9.3 | C4, C5, C6 | PROPOSED / NOT IMPLEMENTED |
| `sbtc_signer_state` | C9.3 | C5, C6 | PROPOSED / NOT IMPLEMENTED |

For `sbtc_signer_state`: **Do not infer signer availability, consensus, security, or custody health unless supported by a validated protocol-defined measure.**

### Transversal surfaces

| Proposed logical source surface | Contextual consumers C1–C8 | Status |
|---|---|---|
| `sbtc_ft_transfers` | C2, C5 | PROPOSED / NOT IMPLEMENTED |
| `sbtc_holder_distribution` | C5 | PROPOSED / NOT IMPLEMENTED |
| `sbtc_dex_trades` | C1, C2, C8 | PROPOSED / NOT IMPLEMENTED |
| `sbtc_amm_pool_state` | C1, C8 | PROPOSED / NOT IMPLEMENTED |
| `sbtc_lending_market_state` | C3, C6 | PROPOSED / NOT IMPLEMENTED |
| `sbtc_protocol_liquidations` | C6, C7 | PROPOSED / NOT IMPLEMENTED |
| `stacks_fee_state` | C6 | PROPOSED / NOT IMPLEMENTED |
| `stacks_mempool_activity` | C6 | PROPOSED / NOT IMPLEMENTED |

C9 does not appear in the contextual-consumer column because C9 owns and
normalizes the Stacks/sBTC data; that column is reserved for downstream
contextual consumers among C1–C8.

## Cross-family semantic guardrails

- Token transfers may inform C2 flow context, but **transfers != CVD automatically**.
  CVD requires an aggressor-side trade model; raw transfers do not encode it.
- Lending activity may contextualize C3 leverage, but **lending != Open
  Interest/Funding**. On-chain debt positions are not derivatives OI or funding
  rates.
- AMM reserves and pool state may contextualize C8 liquidity, but **AMM
  liquidity != order-book depth**. Constant-function pools do not expose a
  central-limit-order-book depth ladder.
- Protocol liquidation events may contextualize C7 stress, but **protocol
  liquidations != derivatives liquidations**. Their triggers, collateral rules
  and execution mechanics differ.

Any future derived mapping must identify its source surface, transformation,
contract version and limitations. It must never relabel a C9 primitive as a
native C1–C8 measurement without an explicit validated derivation.

## Pre-implementation exclusions

At the current repository state:

- the 14 IDs are proposed logical source surfaces, not registered endpoints;
- no Stacks HTTP routes exist;
- no Stacks fixtures exist;
- no Stacks provider exists;
- Milestone 1 has not started;
- `app/endpoint_registry.py` remains limited to the 33 existing C1–C8
  endpoints.

## Future contract requirements

Future funded implementation should define, before adding routes:

- provider-shape adapters for approved Stacks/Hiro/Emily sources;
- normalized timestamps, identifiers, assets, amounts, units and status enums;
- versioned C9 schemas with provenance and data-quality metadata;
- deterministic seeds and scenario IDs for synthetic responses;
- replay ordering, cursor and pagination behavior;
- error, stale-data, reorganization and provider-unavailable scenarios;
- contract parity tests between real-provider shapes and Emulator responses;
- explicit cross-family derivations that enforce the semantic guardrails.

These requirements are design constraints, not evidence of current
implementation.
